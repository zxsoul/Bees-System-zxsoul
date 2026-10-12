"""Daily report synthesis service —— 员工端「零填报」日报的核心编排。

流程（对应工作汇报场景的「机器采、机器写、人只看」）：

1. **聚合**：按 (user, report_date) 从 Redis 个人材料缓冲区取出员工
   当日主动上传的临时材料（截图 / 日志 / 文档）。缓冲区与空间完全
   解耦——材料不属于任何空间，只是员工个人的当日暂存，TTL 24h
   自动清空，**不进入长期知识库**（与「文档管理」的长期存储完全隔离）。
2. **取料**：文本类材料直接解码；截图类材料调用多模态 LLM 做内容识别；
   按材料数均摊长度预算，保证 LLM 上下文可控。
3. **合成**：调用 :class:`LLMGateway` 生成固定四段式 Markdown 日报
   （今日完成 / 进行中与进度 / 阻塞与风险 / 明日计划）。
4. **回流**：日报 Markdown 存入 MinIO 并创建 Document，走与手工上传完全
   一致的 pipeline（解析 → 清洗 → 分块 → 向量化 → 双索引），形成长期
   工作留痕，主管端 RAG 问答因此可以直接检索到日报内容。

失败语义：LLM 调用失败时日报行落为 ``failed``，缓冲区材料不受影响；
单张截图识别失败会降级为占位说明，不阻塞整份日报。
"""

from __future__ import annotations

import asyncio
import io
import logging
import os
import re
import uuid
from datetime import date, datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.exceptions import ForbiddenException, NotFoundException, ValidationException
from app.core.minio import ensure_bucket_exists, get_minio_client
from app.core.redis import get_redis
from app.models.daily_report import DailyReport, DailyReportStatus
from app.models.document import Document, DocumentStatus
from app.models.folder import Folder
from app.models.permission import AccessLevel, Permission, ResourceType
from app.models.space import Space
from app.models.user import User
from app.services.daily_material_service import DailyMaterialService
from app.services.llm_gateway import LLMGateway, LLMGatewayError
from app.services.permission_service import Action, PermissionService

try:
    from app.tasks.pipeline import submit_pipeline as _submit_pipeline
except Exception:  # pragma: no cover — Celery 不可用时 (单元测试) 不触发任务
    _submit_pipeline = None

logger = logging.getLogger(__name__)
settings = get_settings()

# 单份材料贡献给日报合成的最大字符数（按材料数均摊前的单份上限）
MAX_CHARS_PER_MATERIAL = 4000
# 喂给 LLM 的材料总字符数上限，超出部分按时间顺序截断
MAX_TOTAL_MATERIAL_CHARS = 16000
# 单次日报合成最多识别的截图数（每张一次多模态调用，控制耗时）
MAX_IMAGES_PER_REPORT = 6
# 单张截图多模态识别的超时（秒）
IMAGE_DESCRIBE_TIMEOUT = 90
# 日报正文中 LLM 用于插图的占位符（如 [[IMG:M1]]）
IMG_PLACEHOLDER_RE = re.compile(r"\[\[IMG:(M\d+)\]\]")
# 草稿阶段的图片预览链接（指向缓冲区 raw 接口），确认入库时替换为永久链接
PREVIEW_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(/api/daily-materials/([0-9a-f]{32})/raw\)")
# 日报默认归档空间：所有日报统一进入该空间，按日期文件夹组织
DEFAULT_SPACE_NAME = "日报数据库"
# 日报文档标题前缀，用于前端展示标识
REPORT_TITLE_PREFIX = "工作日报"

REPORT_SYSTEM_PROMPT = """你是一名严谨的工作汇报助手。根据员工当天上传的工作材料
（截图文字、日志、文档片段），合成一份客观、结构化的中文工作日报。

硬性要求：
- 只陈述材料中出现的事实，禁止编造材料中不存在的工作内容、数据或结论。
- 每个要点尽量保留可核对的信息：文件名、模块名、数据指标、时间节点。
- 无法从材料判断的部分直接写「材料中未体现」，不要硬凑。
- 插图规则（图文并茂）：每份材料都有编号锚点（[M1]/[M2]/...）。当某张
  截图是数据图表、看板或界面截图、原图比文字描述更有说服力时，在对应
  段落单独一行插入占位符 [[IMG:M1]]（锚点必须与材料编号一致），并配
  一句简短图注；被插图的截图不要再大段复述其中的数据。只允许引用
  kind 为 image 的材料，最多插图 4 张。
- 输出固定四段式 Markdown，不要输出其他任何内容：

# 工作日报 · {author} · {date}

## 一、今日完成
（按事项分条列出，每条附材料依据）

## 二、进行中事项与进度
（事项 + 当前进度/状态）

## 三、阻塞与风险
（没有则写「无」）

## 四、明日计划
（材料中未体现则写「材料中未体现」）
"""


class DailyReportService:
    """日报合成与查询服务。"""

    def __init__(self, db: AsyncSession):
        self.db = db

    # ─── 生成（草稿） ──────────────────────────────────────────────────

    async def generate_daily_report(
        self,
        *,
        user: User,
        space_id: uuid.UUID | None = None,
        report_date: date,
        force: bool = False,
        adjust_prompt: str | None = None,
    ) -> DailyReport:
        """为指定员工合成某天的日报**草稿**（幂等：默认已存在则直接返回）。

        「确认后入库」流程的第一步：只合成内容供员工预览，**不回流入库、
        不转存图片**——插图以缓冲区预览链接呈现（24h 内有效）。员工点击
        「确认使用」后由 :meth:`confirm_report` 执行归档。

        Args:
            user: 日报归属员工
            space_id: 目标空间；None 时进入默认归档空间「日报数据库」
            report_date: 日报日期（按 UTC 日界聚合上传记录）
            force: True 时基于最新材料重新合成并覆盖旧草稿
            adjust_prompt: 调整意见；提供时以上一版草稿为基础按意见重写

        Raises:
            ForbiddenException: 显式指定空间且无写权限
            ValidationException: 当日没有可用的工作材料 / 日报已确认入库
        """
        space = await self._resolve_report_space(user, space_id)
        space_id = space.id

        existing = await self._get_report(space_id, user.id, report_date)
        if existing and existing.confirmed and (force or adjust_prompt):
            raise ValidationException("日报已确认入库，不能再调整或重新生成")
        if (
            existing
            and existing.status == DailyReportStatus.completed
            and not force
            and not adjust_prompt
        ):
            return existing

        # 调整模式下先留住旧草稿内容（合成时作为参考输入）
        prev_content = (
            existing.content if (existing and adjust_prompt) else None
        )

        report = existing or DailyReport(
            space_id=space_id,
            user_id=user.id,
            report_date=report_date,
            title=self._build_title(user, report_date),
            content="",
        )
        report.status = DailyReportStatus.generating
        report.error_detail = None
        self.db.add(report)
        await self.db.flush()

        try:
            buffer_materials = await DailyMaterialService().list_materials_with_content(
                user_id=user.id, day=report_date
            )
            if not buffer_materials:
                raise ValidationException(
                    f"{report_date.isoformat()} 今日缓冲区没有工作材料，"
                    "请先在今日工作台上传截图、日志或文档"
                )

            # 给每份材料一个编号锚点（M1/M2/...），供 LLM 在日报中插图引用
            anchored = [
                (f"M{i}", m) for i, m in enumerate(buffer_materials, start=1)
            ]
            materials = await self._collect_materials(anchored)
            content = await self._synthesize(
                user,
                report_date,
                materials,
                prev_content=prev_content,
                adjust_prompt=adjust_prompt,
            )
            # 图文并茂（草稿阶段）：[[IMG:Mn]] 占位符替换为缓冲区预览链接，
            # 图片永久归档延迟到「确认使用」时执行
            content = self._embed_image_previews(content, dict(anchored))

            report.content = content
            report.source_document_ids = [m["id"] for m in buffer_materials]
            report.title = self._build_title(user, report_date)
            report.status = DailyReportStatus.completed
            report.confirmed = False
            report.confirmed_at = None
            report.generated_at = datetime.now(timezone.utc)
            await self.db.flush()
        except Exception as exc:
            report.status = DailyReportStatus.failed
            report.error_detail = str(exc)[:2000]
            # 先提交失败状态：get_db 依赖在异常时会回滚未提交的事务，
            # 这里主动 commit 让前端能看到「生成失败」并可点「重新生成」。
            await self.db.commit()
            raise

        await self.db.commit()
        await self.db.refresh(report)
        return report

    # ─── 确认入库 ─────────────────────────────────────────────────────

    async def confirm_report(
        self, *, user: User, report_id: uuid.UUID
    ) -> DailyReport:
        """「确认使用」：把草稿日报正式归档入库。

        依次执行：插图转存 MinIO（缓冲区预览链接 → 永久附件链接）→
        按日期文件夹归档日报文档（走完整 pipeline，主管端可检索）→
        标记 confirmed。幂等：已确认的直接返回。
        """
        stmt = select(DailyReport).where(DailyReport.id == report_id)
        report = (await self.db.execute(stmt)).scalar_one_or_none()
        if not report:
            raise NotFoundException("DailyReport", str(report_id))
        if report.user_id != user.id and not self._is_admin(user):
            raise ForbiddenException("只能确认自己的日报")
        if report.status != DailyReportStatus.completed:
            raise ValidationException("日报尚未生成完成，无法确认入库")
        if report.confirmed:
            return report

        content = await self._persist_images(report, report.content)
        folder = await self._get_or_create_date_folder(
            report.space_id, report.report_date
        )
        document = await self._ingest_report_document(
            report, content, folder_id=folder.id
        )

        report.content = content
        report.document_id = document.id
        report.confirmed = True
        report.confirmed_at = datetime.now(timezone.utc)
        await self.db.commit()
        await self.db.refresh(report)
        return report

    # ─── 查询 ─────────────────────────────────────────────────────────

    async def list_my_reports(
        self,
        *,
        user_id: uuid.UUID,
        space_id: uuid.UUID | None = None,
        skip: int = 0,
        limit: int = 20,
    ) -> tuple[list[DailyReport], int]:
        """员工查看自己的历史日报。"""
        stmt = select(DailyReport).where(DailyReport.user_id == user_id)
        count_stmt = select(func.count(DailyReport.id)).where(
            DailyReport.user_id == user_id
        )
        if space_id:
            stmt = stmt.where(DailyReport.space_id == space_id)
            count_stmt = count_stmt.where(DailyReport.space_id == space_id)
        stmt = stmt.order_by(DailyReport.report_date.desc()).offset(skip).limit(limit)

        total = (await self.db.execute(count_stmt)).scalar_one()
        rows = (await self.db.execute(stmt)).scalars().all()
        return list(rows), total

    async def list_team_reports(
        self,
        *,
        requester: User,
        space_id: uuid.UUID,
        report_date: date | None = None,
        skip: int = 0,
        limit: int = 50,
    ) -> tuple[list[DailyReport], int]:
        """主管查看空间（部门/项目）内全员日报。

        鉴权：系统管理员或空间创建者（主管）。普通成员只有 read/write
        材料权限，无法查看他人日报——天然实现「成员看自己、主管看全员」。
        """
        await self.require_space_manager(requester, space_id)

        stmt = select(DailyReport).where(DailyReport.space_id == space_id)
        count_stmt = select(func.count(DailyReport.id)).where(
            DailyReport.space_id == space_id
        )
        if report_date:
            stmt = stmt.where(DailyReport.report_date == report_date)
            count_stmt = count_stmt.where(DailyReport.report_date == report_date)
        stmt = stmt.order_by(
            DailyReport.report_date.desc(), DailyReport.created_at.desc()
        ).offset(skip).limit(limit)

        total = (await self.db.execute(count_stmt)).scalar_one()
        rows = (await self.db.execute(stmt)).scalars().all()
        return list(rows), total

    async def get_report(
        self, *, requester: User, report_id: uuid.UUID
    ) -> DailyReport:
        """查看单份日报：本人或空间主管（空间创建者/管理员）。"""
        stmt = select(DailyReport).where(DailyReport.id == report_id)
        report = (await self.db.execute(stmt)).scalar_one_or_none()
        if not report:
            raise NotFoundException("DailyReport", str(report_id))

        if report.user_id != requester.id:
            await self.require_space_manager(requester, report.space_id)
        return report

    # ─── 内部：材料聚合（Redis 缓冲区） ───────────────────────────────

    async def _collect_materials(self, anchored: list[tuple[str, dict]]) -> str:
        """把缓冲区材料整理成 LLM 输入文本。

        - 每份材料带编号锚点（[M1]/[M2]/...），LLM 插图时引用；
        - text 类：直接 UTF-8 解码，按均摊预算截断；
        - image 类：调用多模态 LLM 识别截图内容（单张失败降级为占位说明，
          不阻塞整份日报）；
        - other 类：仅记录文件名，提示内容未解析。
        """
        per_material_budget = max(
            500,
            min(MAX_CHARS_PER_MATERIAL, MAX_TOTAL_MATERIAL_CHARS // max(len(anchored), 1)),
        )
        sections: list[str] = []
        total = 0
        image_count = 0
        for anchor, m in anchored:
            uploaded_hm = (m.get("uploaded_at") or "")[11:16]
            header = (
                f"### 材料 [{anchor}]：{m['filename']}"
                f"（{m['kind']}，上传于 {uploaded_hm} UTC）"
            )

            if m["kind"] == "text":
                text = DailyMaterialService.extract_text(m, per_material_budget)
            elif m["kind"] == "image":
                if image_count >= MAX_IMAGES_PER_REPORT:
                    text = "（截图数量超出单次合成上限，未识别内容）"
                else:
                    image_count += 1
                    text = await self._resolve_image_description(m)
            else:
                # other（PDF/DOCX 等）：优先用上传后异步提取的文本缓存
                text = (m.get("extracted_text") or "").strip()
                if not text:
                    text = "（暂不支持解析该文件类型，仅记录文件名）"

            if not text.strip():
                continue
            section = f"{header}\n{text}"
            if total + len(section) > MAX_TOTAL_MATERIAL_CHARS:
                break
            sections.append(section)
            total += len(section)

        if not sections:
            raise ValidationException(
                "缓冲区材料内容为空或无法解析，请重新上传文本/截图材料"
            )
        return "\n\n".join(sections)

    async def _resolve_image_description(self, material: dict) -> str:
        """取截图的识别描述：优先用上传后异步识别的缓存结果。

        上传即预处理（``daily_materials.analyze`` 任务）通常已完成；
        刚上传就点生成时，对 pending/processing 状态短暂轮询等待
        （每 2s、最多 30s）；超时或识别失败则回退到同步识别兜底。
        """
        if material.get("ai_description"):
            return material["ai_description"]

        if material.get("desc_status") in ("pending", "processing"):
            service = DailyMaterialService()
            for _ in range(15):
                await asyncio.sleep(2)
                refreshed = await service.get_material(material["id"])
                if not refreshed:
                    break
                if refreshed.get("ai_description"):
                    return refreshed["ai_description"]
                if refreshed.get("desc_status") not in ("pending", "processing"):
                    break
                material = refreshed

        # 兜底：异步结果不可用，退回同步识别（保持既有降级语义）
        return await self._describe_image(material)

    async def _describe_image(self, material: dict) -> str:
        """多模态识别截图内容；任何失败都降级为占位说明。"""
        from app.core.config import get_settings

        data = DailyMaterialService.material_bytes(material)
        mime = material.get("content_type") or "image/png"
        # 截图识别需要视觉能力 + 准确读数，固定走 REPORT_MODEL（如 kimi-k2.6）
        report_model = get_settings().REPORT_MODEL or None
        gateway = LLMGateway(model=report_model, timeout=120)
        try:
            resp = await asyncio.wait_for(
                gateway.complete_multimodal(
                    prompt=(
                        "这是一张员工的工作截图。请客观描述截图中的内容："
                        "涉及的系统/界面/模块名称、可见的文字要点、数据指标、"
                        "代码或报错信息等。只陈述截图里能看到的内容，"
                        "不要推测，200 字以内。"
                    ),
                    images=[data],
                    image_mime_type=mime,
                    max_tokens=1024,
                ),
                timeout=IMAGE_DESCRIBE_TIMEOUT,
            )
            content = (resp.content or "").strip()
            if content:
                return f"【截图内容识别】{content}"
        except Exception as exc:  # noqa: BLE001 — 单张失败不阻塞日报
            logger.warning(
                "daily report image describe failed: material=%s error=%s",
                material.get("id"),
                exc,
            )
        return "（截图内容识别失败，仅记录截图文件名）"

    # ─── 内部：图文并茂（草稿预览 / 确认归档两阶段） ───────────────────

    @staticmethod
    def _embed_image_previews(
        content: str, materials_by_anchor: dict[str, dict]
    ) -> str:
        """草稿阶段：把 ``[[IMG:Mn]]`` 占位符替换为缓冲区预览链接。

        预览链接指向 ``/api/daily-materials/{id}/raw``（24h 内有效），
        图片永久归档延迟到「确认使用」时由 ``_persist_images`` 完成。
        未命中材料 / 非图片的占位符一律清除，不留残渣。
        """
        refs = IMG_PLACEHOLDER_RE.findall(content)
        for anchor in dict.fromkeys(refs):  # 去重且保留出现顺序
            placeholder = f"[[IMG:{anchor}]]"
            record = materials_by_anchor.get(anchor)
            if not record or record.get("kind") != "image":
                logger.info("report image placeholder unresolved: %s", anchor)
                content = content.replace(placeholder, "")
                continue
            image_md = (
                f"![{record['filename']}]"
                f"(/api/daily-materials/{record['id']}/raw)"
            )
            content = content.replace(placeholder, image_md)
        return content

    async def _persist_images(self, report: DailyReport, content: str) -> str:
        """确认入库阶段：把草稿中的缓冲区预览图片转存 MinIO 长期保存。

        预览链接 → ``/api/reports/{id}/assets/{material_id}`` 永久链接。
        缓冲区已过期的图片替换为文字说明（不抛错，不阻塞入库）。
        """
        matches = list(PREVIEW_IMAGE_RE.finditer(content))
        if not matches:
            return content

        client = get_minio_client()
        ensure_bucket_exists()
        svc = DailyMaterialService()
        for match in matches:
            alt, material_id = match.group(1), match.group(2)
            record = await svc.get_material(material_id)
            replacement: str | None = None
            if record and record.get("kind") == "image":
                ext = (
                    record["filename"].rsplit(".", 1)[-1].lower()
                    if "." in record["filename"]
                    else "png"
                )
                storage_key = f"report-assets/{report.id}/{material_id}.{ext}"
                try:
                    client.upload_fileobj(
                        Fileobj=io.BytesIO(
                            DailyMaterialService.material_bytes(record)
                        ),
                        Bucket=settings.MINIO_BUCKET,
                        Key=storage_key,
                        ExtraArgs={
                            "ContentType": record.get("content_type") or "image/png"
                        },
                    )
                    replacement = (
                        f"![{alt}](/api/reports/{report.id}/assets/{material_id})"
                    )
                    logger.info(
                        "report asset persisted: report=%s material=%s key=%s",
                        report.id,
                        material_id,
                        storage_key,
                    )
                except Exception:
                    logger.warning(
                        "report asset persist failed: report=%s material=%s",
                        report.id,
                        material_id,
                        exc_info=True,
                    )
            if replacement is None:
                replacement = f"（图片「{alt}」的缓冲材料已过期，未能归档）"
            content = content.replace(match.group(0), replacement)
        return content

    async def _get_or_create_date_folder(
        self, space_id: uuid.UUID, report_date: date
    ) -> Folder:
        """在归档空间下找/建以日期命名的根目录文件夹（如 ``2026-10-12``）。

        「日报数据库」空间的组织方式：按日期文件夹分组，打开某天即可看到
        当日全员日报（类似文件夹的时间线视图）。
        """
        name = report_date.isoformat()
        stmt = select(Folder).where(
            Folder.space_id == space_id,
            Folder.parent_id.is_(None),
            Folder.name == name,
        )
        folder = (await self.db.execute(stmt)).scalar_one_or_none()
        if folder:
            return folder
        folder = Folder(space_id=space_id, name=name, depth=1)
        self.db.add(folder)
        await self.db.flush()
        return folder

    # ─── 内部：LLM 合成 ────────────────────────────────────────────────

    async def _synthesize(
        self,
        user: User,
        report_date: date,
        materials: str,
        prev_content: str | None = None,
        adjust_prompt: str | None = None,
    ) -> str:
        author = user.display_name or user.email
        system_prompt = REPORT_SYSTEM_PROMPT.format(
            author=author, date=report_date.isoformat()
        )
        # 日报是大段正式写作，固定走 REPORT_MODEL（如 kimi-k2.6）；
        # 长输入下生成耗时可能超过默认 60s 超时，显式放宽
        from app.core.config import get_settings

        report_model = get_settings().REPORT_MODEL or None
        gateway = LLMGateway(model=report_model, timeout=180)
        prompt = (
            f"以下是 {author} 在 {report_date.isoformat()} 上传的"
            f"工作材料，请合成当日工作日报：\n\n{materials}"
        )
        if adjust_prompt and prev_content:
            prompt += (
                f"\n\n---\n以下是上一版日报草稿：\n\n{prev_content}\n\n---\n"
                f"请根据以下调整意见重新生成日报（事实仍以材料为准，只按意见"
                f"调整结构/详略/表述）：\n{adjust_prompt}"
            )
        # 思考型模型（如 kimi-k2.6）会把推理过程计入 completion tokens：
        # 预算给小了会出现「思考耗尽预算、正文为空」的情况（finish_reason=length），
        # 因此按阶梯预算重试，直到拿到非空正文。
        last_error: Exception | None = None
        for max_tokens in (2048, 8192, 16384):
            try:
                resp = await gateway.complete(
                    prompt=prompt,
                    system_prompt=system_prompt,
                    max_tokens=max_tokens,
                )
            except LLMGatewayError as exc:
                last_error = exc
                # 超时/限流值得用更大预算再试一次；鉴权类错误重试无意义
                if exc.reason in ("auth", "model_unavailable"):
                    break
                continue
            content = (resp.content or "").strip()
            if content:
                return content
            logger.warning(
                "daily report synthesis empty content: max_tokens=%d finish=%s",
                max_tokens,
                resp.finish_reason,
            )
        if last_error is not None:
            raise ValidationException(
                f"日报合成失败（LLM 网关: {last_error.reason}），请稍后重试"
            ) from last_error
        raise ValidationException("日报合成失败：模型多次返回空内容，请稍后重试")

    # ─── 内部：日报回流入库 ────────────────────────────────────────────

    async def _ingest_report_document(
        self, report: DailyReport, content: str, folder_id: uuid.UUID | None = None
    ) -> Document:
        """把日报 Markdown 作为新 Document 走完整 pipeline 入库。

        与 ``UploadService.import_url`` 保持同一语义：先提交 DB 再入队，
        保证 worker 拉取时一定能从 DB 读到文档记录。
        ``folder_id`` 指向按日期命名的归档文件夹。
        """
        content_bytes = content.encode("utf-8")
        storage_path = f"{report.space_id}/{uuid.uuid4()}/{report.title}.md"

        client = get_minio_client()
        ensure_bucket_exists()
        client.upload_fileobj(
            Fileobj=io.BytesIO(content_bytes),
            Bucket=settings.MINIO_BUCKET,
            Key=storage_path,
            ExtraArgs={"ContentType": "text/markdown"},
        )

        document = Document(
            space_id=report.space_id,
            folder_id=folder_id,
            title=report.title,
            file_type="md",
            file_size=len(content_bytes),
            storage_path=storage_path,
            status=DocumentStatus.pending,
            retry_count=0,
            uploaded_by=report.user_id,
        )
        self.db.add(document)
        await self.db.flush()
        # 先提交，让 worker 可见；日报行的 document_id 关联由调用方随后 flush
        await self.db.commit()

        if _submit_pipeline is not None:
            try:
                await asyncio.to_thread(_submit_pipeline, str(document.id))
            except Exception:
                logger.warning(
                    "daily_report pipeline submit failed: document_id=%s",
                    document.id,
                    exc_info=True,
                )
        return document

    # ─── 内部：通用 ────────────────────────────────────────────────────

    async def _resolve_report_space(
        self, user: User, space_id: uuid.UUID | None
    ) -> Space:
        """解析日报归档空间。

        - 显式传入 space_id：严格校验写权限（兼容旧调用）；
        - 缺省：进入默认归档空间「日报数据库」，不存在则以管理员身份创建，
          并自动为员工补齐该空间的写权限（默认空间对全员开放归档）。
        """
        if space_id is not None:
            await self._require_space_action(user, space_id, Action.write)
            space = (
                await self.db.execute(select(Space).where(Space.id == space_id))
            ).scalar_one_or_none()
            if space is None:
                raise NotFoundException("Space", str(space_id))
            return space

        stmt = select(Space).where(Space.name == DEFAULT_SPACE_NAME)
        space = (await self.db.execute(stmt)).scalar_one_or_none()
        if space is None:
            creator_id = await self._admin_user_id() or user.id
            space = Space(
                name=DEFAULT_SPACE_NAME,
                description="每日工作日报自动归档（按日期文件夹组织）",
                created_by=creator_id,
            )
            self.db.add(space)
            await self.db.flush()
            logger.info("default report space created: %s", space.id)

        await self._ensure_space_write(user, space.id)
        return space

    async def _admin_user_id(self) -> uuid.UUID | None:
        """系统管理员（INITIAL_ADMIN_EMAIL）的用户 ID，用于默认空间归属。"""
        email = os.environ.get("INITIAL_ADMIN_EMAIL", "").strip().lower()
        if not email:
            return None
        stmt = select(User.id).where(func.lower(User.email) == email)
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def _ensure_space_write(
        self, user: User, space_id: uuid.UUID
    ) -> None:
        """确保员工对默认归档空间有写权限（缺失则自动授予）。"""
        if self._is_admin(user):
            return
        redis = await get_redis()
        perm = PermissionService(self.db, redis)
        allowed = await perm.check_access(
            user_id=user.id,
            resource_id=space_id,
            resource_type=ResourceType.space,
            action=Action.write,
        )
        if allowed:
            return
        try:
            # 走正式授权链路：upsert + 缓存失效 + Qdrant 权限同步
            await perm.set_space_permission(
                space_id=space_id,
                user_id=user.id,
                access_level=AccessLevel.write,
            )
        except Exception:
            # Qdrant 同步失败等场景兜底：直接落库写权限并清缓存，
            # 不阻塞日报生成（检索侧权限同步由 watchdog/下次授权补齐）
            logger.warning(
                "auto grant write via PermissionService failed, fallback to direct insert",
                exc_info=True,
            )
            await self.db.rollback()
            stmt = select(Permission).where(
                Permission.resource_id == space_id,
                Permission.resource_type == ResourceType.space,
                Permission.user_id == user.id,
            )
            row = (await self.db.execute(stmt)).scalar_one_or_none()
            if row:
                row.access_level = AccessLevel.write
            else:
                self.db.add(
                    Permission(
                        resource_id=space_id,
                        resource_type=ResourceType.space,
                        user_id=user.id,
                        access_level=AccessLevel.write,
                    )
                )
            await self.db.flush()
            await perm._invalidate_space_cache(user.id, space_id)

    async def _get_report(
        self, space_id: uuid.UUID, user_id: uuid.UUID, report_date: date
    ) -> DailyReport | None:
        stmt = select(DailyReport).where(
            DailyReport.space_id == space_id,
            DailyReport.user_id == user_id,
            DailyReport.report_date == report_date,
        )
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def _require_space_action(
        self, user: User, space_id: uuid.UUID, action: Action
    ) -> None:
        """空间级 ABAC 校验；管理员邮箱绕过（与 require_admin 同一口径）。"""
        if self._is_admin(user):
            return
        redis = await get_redis()
        perm = PermissionService(self.db, redis)
        allowed = await perm.check_access(
            user_id=user.id,
            resource_id=space_id,
            resource_type=ResourceType.space,
            action=action,
        )
        if not allowed:
            raise ForbiddenException("没有该空间的操作权限")

    async def require_space_manager(
        self, user: User, space_id: uuid.UUID
    ) -> None:
        """「主管」判定：系统管理员或空间创建者。

        为什么不用 write 权限：员工上传材料本身就需要 write，如果全员视图
        也用 write 守门，任何成员都能看所有人的日报。空间创建者即团队主管，
        是现有模型里唯一能把「主管」和「成员」区分开的信号；管理员在
        「系统管理 → 权限配置」里把空间转授给主管即完成授权。
        """
        if self._is_admin(user):
            return
        stmt = select(Space.created_by).where(Space.id == space_id)
        creator_id = (await self.db.execute(stmt)).scalar_one_or_none()
        if creator_id is None:
            raise NotFoundException("Space", str(space_id))
        if creator_id != user.id:
            raise ForbiddenException("需要该空间的主管权限")

    @staticmethod
    def _is_admin(user: User) -> bool:
        admin_email = os.environ.get("INITIAL_ADMIN_EMAIL", "").strip().lower()
        return bool(admin_email) and (user.email or "").strip().lower() == admin_email

    @staticmethod
    def _build_title(user: User, report_date: date) -> str:
        author = user.display_name or user.email.split("@")[0]
        return f"{REPORT_TITLE_PREFIX}-{author}-{report_date.isoformat()}"
