"""工作日报相关的 API 路由（工作汇报场景 · 员工端 + 主管端）。

- ``POST /api/reports/generate``：员工为自己生成某天的日报（幂等）
- ``GET  /api/reports/my``：员工查看自己的历史日报
- ``GET  /api/reports/team``：主管查看空间内全员日报（空间创建者/管理员）
- ``GET  /api/reports/{report_id}``：查看单份日报（本人或空间主管）
- ``GET  /api/reports/{report_id}/assets/{material_id}``：日报内嵌图片附件

鉴权：全员接口依赖 :func:`get_current_user`；越权防护在
:class:`DailyReportService` 内完成——员工生成自己的日报需要空间 write
权限（与上传材料一致），「全员视图」放行：空间创建者（主管）、系统
管理员，或被管理员授予 ``can_view_team_reports`` 的用户（可跨空间
无差别查看）。图片附件与日报详情同一权限口径。
"""

import asyncio
import logging
import uuid
from datetime import date, datetime

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import get_current_user
from app.core.config import get_settings
from app.core.database import get_db
from app.core.exceptions import NotFoundException
from app.core.minio import get_minio_client
from app.models.daily_report import DailyReport
from app.models.user import User
from app.services.report_service import DailyReportService

logger = logging.getLogger(__name__)
settings = get_settings()

router = APIRouter(tags=["reports"])


# ─── Request / Response Schemas ──────────────────────────────────────


class GenerateReportRequest(BaseModel):
    """日报生成请求。"""

    space_id: str | None = Field(
        None, description="目标空间 ID；缺省进入默认归档空间「日报数据库」"
    )
    report_date: date | None = Field(
        None, description="日报日期，缺省为今天（UTC）"
    )
    force: bool = Field(
        False, description="已存在草稿时是否基于最新材料重新生成"
    )
    adjust_prompt: str | None = Field(
        None, description="调整意见：提供时以上一版草稿为基础按意见重新生成"
    )


class DailyReportResponse(BaseModel):
    """单份日报。"""

    id: str
    space_id: str
    user_id: str
    author: str
    report_date: date
    title: str
    content: str
    source_document_ids: list[str]
    document_id: str | None
    status: str
    error_detail: str | None
    generated_at: datetime | None
    confirmed: bool
    confirmed_at: datetime | None
    created_at: datetime


class DailyReportListResponse(BaseModel):
    """日报列表。"""

    reports: list[DailyReportResponse]
    total: int


# ─── Helpers ──────────────────────────────────────────────────────────


def _to_response(report: DailyReport) -> DailyReportResponse:
    author = ""
    if report.user:
        author = report.user.display_name or report.user.email
    return DailyReportResponse(
        id=str(report.id),
        space_id=str(report.space_id),
        user_id=str(report.user_id),
        author=author,
        report_date=report.report_date,
        title=report.title,
        content=report.content,
        source_document_ids=report.source_document_ids or [],
        document_id=str(report.document_id) if report.document_id else None,
        status=report.status.value,
        error_detail=report.error_detail,
        generated_at=report.generated_at,
        confirmed=report.confirmed,
        confirmed_at=report.confirmed_at,
        created_at=report.created_at,
    )


# ─── Endpoints ────────────────────────────────────────────────────────


@router.post("/api/reports/generate", response_model=DailyReportResponse)
async def generate_report(
    request: GenerateReportRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> DailyReportResponse:
    """生成当日（或指定日）我的工作日报**草稿**。

    聚合当天本人缓冲区的全部工作材料（截图/日志/文档），LLM 合成
    图文并茂的四段式日报草稿。草稿**不入库**——员工预览后可：
    - 「确认使用」→ ``POST /api/reports/{id}/confirm`` 正式归档；
    - 「调整」→ 带 ``adjust_prompt`` 重新生成（以上一版为基础）。
    同一天重复调用默认返回已有草稿（幂等），``force=true`` 强制重算。
    已确认入库的日报不可再调整。
    """
    service = DailyReportService(db)
    report = await service.generate_daily_report(
        user=current_user,
        space_id=uuid.UUID(request.space_id) if request.space_id else None,
        report_date=request.report_date or date.today(),
        force=request.force,
        adjust_prompt=request.adjust_prompt,
    )
    return _to_response(report)


@router.get("/api/reports/my", response_model=DailyReportListResponse)
async def list_my_reports(
    space_id: str | None = Query(None, description="按空间过滤"),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> DailyReportListResponse:
    """员工查看自己的历史日报列表。"""
    service = DailyReportService(db)
    reports, total = await service.list_my_reports(
        user_id=current_user.id,
        space_id=uuid.UUID(space_id) if space_id else None,
        skip=skip,
        limit=limit,
    )
    return DailyReportListResponse(
        reports=[_to_response(r) for r in reports], total=total
    )


@router.get("/api/reports/team", response_model=DailyReportListResponse)
async def list_team_reports(
    space_id: str | None = Query(
        None,
        description="目标空间 ID；被授权的全员日报查看者可缺省，跨空间查看全部",
    ),
    report_date: date | None = Query(None, description="按日期过滤"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> DailyReportListResponse:
    """查看全员日报（需空间主管权限或被授予全员日报查看权限，否则 403）。"""
    service = DailyReportService(db)
    reports, total = await service.list_team_reports(
        requester=current_user,
        space_id=uuid.UUID(space_id) if space_id else None,
        report_date=report_date,
        skip=skip,
        limit=limit,
    )
    return DailyReportListResponse(
        reports=[_to_response(r) for r in reports], total=total
    )


@router.post("/api/reports/{report_id}/confirm", response_model=DailyReportResponse)
async def confirm_report(
    report_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> DailyReportResponse:
    """「确认使用」：草稿日报正式入库。

    插图从缓冲区转存 MinIO 长期归档，日报文档写入「日报数据库」空间的
    当日日期文件夹并走完整 pipeline（主管端可检索）。幂等。
    """
    service = DailyReportService(db)
    report = await service.confirm_report(
        user=current_user, report_id=uuid.UUID(report_id)
    )
    return _to_response(report)


@router.get("/api/reports/{report_id}", response_model=DailyReportResponse)
async def get_report(
    report_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> DailyReportResponse:
    """查看单份日报详情：本人或空间主管（write 权限）。"""
    service = DailyReportService(db)
    report = await service.get_report(
        requester=current_user, report_id=uuid.UUID(report_id)
    )
    return _to_response(report)


@router.get("/api/reports/{report_id}/assets/{material_id}")
async def get_report_asset(
    report_id: str,
    material_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """获取日报内嵌的图片附件（图文并茂）。

    图片在日报生成时从缓冲区转存到 MinIO（``report-assets/{日报ID}/``），
    长期保存；权限口径与查看日报一致（本人或空间主管）。
    """
    service = DailyReportService(db)
    # 复用日报查看权限（本人或空间主管），越权会在这里抛 403/404
    await service.get_report(requester=current_user, report_id=uuid.UUID(report_id))

    client = get_minio_client()
    prefix = f"report-assets/{report_id}/{material_id}."
    listing = await asyncio.to_thread(
        client.list_objects_v2,
        Bucket=settings.MINIO_BUCKET,
        Prefix=prefix,
        MaxKeys=1,
    )
    contents = listing.get("Contents") or []
    if not contents:
        raise NotFoundException("ReportAsset", material_id)

    obj = await asyncio.to_thread(
        client.get_object, Bucket=settings.MINIO_BUCKET, Key=contents[0]["Key"]
    )
    data = await asyncio.to_thread(obj["Body"].read)
    return Response(
        content=data,
        media_type=obj.get("ContentType") or "image/png",
        headers={"Cache-Control": "private, max-age=3600"},
    )
