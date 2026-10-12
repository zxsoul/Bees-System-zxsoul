"""Daily materials API —— 今日速汇报的材料缓冲区接口。

缓冲区是**纯个人暂存区，与空间完全解耦**：
- 上传的材料只进 Redis 缓冲区（TTL 24h，隔夜自动清空），不走文档管线、
  不落库、不出现在「文档管理」里，也不关联任何空间；
- 任何登录用户都可以随意上传/查看/删除**自己的**材料，无需空间权限；
- 材料仅用于当日日报合成（生成日报时才按空间聚合归属）。

查看接口供「点击跳转查看」页面使用（文本直接返回内容，
图片走 ``/raw`` 取原始字节预览）。
"""

from __future__ import annotations

import logging
from datetime import date

from fastapi import APIRouter, Depends, File, Query, Response, UploadFile
from pydantic import BaseModel

from app.api.auth import get_current_user
from app.core.exceptions import ForbiddenException, NotFoundException, ValidationException
from app.models.user import User
from app.services.daily_material_service import DailyMaterialService

logger = logging.getLogger(__name__)

router = APIRouter(tags=["daily-materials"])


# ─── Schemas ─────────────────────────────────────────────────────────


class DailyMaterialResponse(BaseModel):
    id: str
    user_id: str
    date: str
    filename: str
    content_type: str
    size: int
    kind: str  # text / image / other
    uploaded_at: str
    # 异步识别状态：pending/processing/done/failed（text 类上传即 done）
    desc_status: str = "pending"
    # 识别结果（图片的 AI 描述 / 文档提取文本），供前端展示与人工核对
    ai_description: str | None = None
    extracted_text: str | None = None


class DailyMaterialListResponse(BaseModel):
    materials: list[DailyMaterialResponse]
    total: int


class DailyMaterialDetailResponse(DailyMaterialResponse):
    """查看页用：文本类材料附带正文内容（截断），图片/其它为 None。"""

    text_content: str | None = None


def _check_owner(record: dict, user: User) -> None:
    if record["user_id"] != str(user.id):
        raise ForbiddenException("只能操作自己上传的材料")


# ─── 上传 ────────────────────────────────────────────────────────────


@router.post("/api/daily-materials/upload", response_model=DailyMaterialResponse)
async def upload_material(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
):
    """上传一份今日工作材料到我的缓冲区（仅暂存，与空间无关）。

    图片 / 文档类材料上传成功后立即投递异步识别任务（后台预处理），
    生成日报时直接使用识别结果，无需等待逐张识别。
    """
    data = await file.read()
    service = DailyMaterialService()
    try:
        meta = await service.add_material(
            user_id=current_user.id,
            filename=file.filename or "未命名文件",
            content_type=file.content_type,
            data=data,
        )
    except ValueError as exc:
        raise ValidationException(str(exc)) from exc

    # 上传即预处理：image 走视觉识别，other 走文本提取；text 类无需任务
    if meta["kind"] in ("image", "other"):
        try:
            from app.core.celery_app import celery_app

            celery_app.send_task("daily_materials.analyze", args=[meta["id"]])
        except Exception as exc:  # noqa: BLE001 - 投递失败不阻塞上传
            logger.warning(
                "daily material analyze dispatch failed: id=%s error=%s",
                meta["id"],
                exc,
            )
    return meta


# ─── 列表 / 查看 ─────────────────────────────────────────────────────


@router.get("/api/daily-materials", response_model=DailyMaterialListResponse)
async def list_materials(
    report_date: date | None = Query(None, description="缺省为今天（UTC）"),
    current_user: User = Depends(get_current_user),
):
    """列出我当日缓冲区里的全部材料。"""
    service = DailyMaterialService()
    materials = await service.list_materials(
        user_id=current_user.id,
        day=report_date,
    )
    return DailyMaterialListResponse(materials=materials, total=len(materials))


@router.get(
    "/api/daily-materials/{material_id}", response_model=DailyMaterialDetailResponse
)
async def get_material(
    material_id: str,
    current_user: User = Depends(get_current_user),
):
    """查看单份材料：文本类附正文，图片请走 /raw 预览。"""
    service = DailyMaterialService()
    record = await service.get_material(material_id)
    if not record:
        raise NotFoundException("DailyMaterial", material_id)
    _check_owner(record, current_user)

    text_content = None
    if record["kind"] == "text":
        text_content = DailyMaterialService.extract_text(record)
    return DailyMaterialDetailResponse(
        **{k: v for k, v in record.items() if k != "content_b64"},
        text_content=text_content,
    )


@router.get("/api/daily-materials/{material_id}/raw")
async def get_material_raw(
    material_id: str,
    current_user: User = Depends(get_current_user),
):
    """返回材料原始字节（图片预览 / 文件下载）。"""
    service = DailyMaterialService()
    record = await service.get_material(material_id)
    if not record:
        raise NotFoundException("DailyMaterial", material_id)
    _check_owner(record, current_user)

    return Response(
        content=DailyMaterialService.material_bytes(record),
        media_type=record["content_type"],
        headers={
            "Content-Disposition": f'inline; filename="{record["filename"]}"'
        },
    )


# ─── 删除 ────────────────────────────────────────────────────────────


@router.delete("/api/daily-materials/{material_id}", status_code=204)
async def delete_material(
    material_id: str,
    current_user: User = Depends(get_current_user),
):
    """删除一份误传的材料（仅本人，删后可重新上传）。"""
    service = DailyMaterialService()
    record = await service.get_material(material_id)
    if not record:
        raise NotFoundException("DailyMaterial", material_id)
    _check_owner(record, current_user)
    await service.delete_material(material_id=material_id, user_id=current_user.id)
    return None
