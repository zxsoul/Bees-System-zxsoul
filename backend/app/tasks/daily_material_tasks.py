"""今日材料缓冲区的异步识别任务（上传即预处理）。

设计动机：
员工上传截图/文档后，不要等到「生成日报」时才逐张调视觉模型识别
（每张 3~5s，多张串行可达 30s+，且「调整重生成」会重复识别同一张图）。
改为上传成功后立即投递本任务，后台完成识别并把结果写回 Redis 材料
记录（``ai_description`` / ``extracted_text`` / ``desc_status``）；
生成日报时直接取用缓存结果，只跑一次综合生成调用。

任务边界：
- 只处理缓冲区（Redis）里的材料，与文档管线、知识库完全无关；
- 单个材料失败仅标记 ``desc_status=failed``，日报生成时降级为占位
  说明（与 report_service 既有降级语义一致），不重试阻塞队列。
"""

from __future__ import annotations

import asyncio
import logging

from app.core.celery_app import celery_app

logger = logging.getLogger(__name__)

# 与 report_service.IMAGE_DESCRIBE_TIMEOUT 对齐
_DESCRIBE_TIMEOUT = 120.0
# 文本提取预算（与 DailyMaterialService.extract_text 默认一致）
_MAX_EXTRACT_CHARS = 8000

_IMAGE_PROMPT = (
    "这是一张员工的工作截图。请客观描述截图中的内容："
    "涉及的系统/界面/模块名称、可见的文字要点、数据指标、"
    "代码或报错信息等。只陈述截图里能看到的内容，"
    "不要推测，200 字以内。"
)


# 进程级持久事件循环：worker 子进程内复用同一 loop 跑所有任务。
# 不能「每任务新建 loop 用完即关」——Redis/DB 客户端是全局单例，
# 首次使用会绑定创建时的 loop；loop 关闭后，后续任务再用这些客户端
# 会抛 RuntimeError: Event loop is closed（本项目已实际踩中）。
_WORKER_LOOP: asyncio.AbstractEventLoop | None = None


def _run_async(coro):
    """在 worker 子进程的持久事件循环上运行异步协程。"""
    global _WORKER_LOOP
    if _WORKER_LOOP is None or _WORKER_LOOP.is_closed():
        _WORKER_LOOP = asyncio.new_event_loop()
        asyncio.set_event_loop(_WORKER_LOOP)
    return _WORKER_LOOP.run_until_complete(coro)


@celery_app.task(
    name="daily_materials.analyze",
    soft_time_limit=150,
    time_limit=180,
    max_retries=0,
)
def analyze_daily_material(material_id: str) -> dict:
    """识别一份缓冲区材料并把结果写回 Redis。

    - image：调用视觉模型（REPORT_MODEL）生成内容描述；
    - other：PDF/DOCX 本地提取文本（不消耗 LLM）；
    - text：上传时已同步填充，无需处理。

    Returns:
        ``{"material_id": ..., "status": done/failed/skipped/missing}``
    """
    async def _run() -> dict:
        from app.services.daily_material_service import DailyMaterialService

        service = DailyMaterialService()
        record = await service.get_material(material_id)
        if not record:
            return {"material_id": material_id, "status": "missing"}

        kind = record.get("kind")
        if kind == "text":
            return {"material_id": material_id, "status": "skipped"}

        await service.update_analysis(material_id, desc_status="processing")

        if kind == "image":
            return await _analyze_image(service, record)
        return await _extract_document(service, record)

    # 顶层兜底：任务意外崩溃时尽量把状态标记为 failed，
    # 避免材料永远停在 pending/processing（前端会一直显示「识别中」）。
    try:
        return _run_async(_run())
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "daily material analyze crashed: id=%s error=%s",
            material_id,
            exc,
            exc_info=True,
        )
        try:
            from app.services.daily_material_service import DailyMaterialService

            _run_async(
                DailyMaterialService().update_analysis(
                    material_id, desc_status="failed"
                )
            )
        except Exception:  # noqa: BLE001
            pass
        return {"material_id": material_id, "status": "failed"}


async def _analyze_image(service, record: dict) -> dict:
    """视觉模型识别截图，成功写 ai_description，失败标 failed。"""
    from app.core.config import get_settings
    from app.services.daily_material_service import DailyMaterialService
    from app.services.llm_gateway import LLMGateway

    material_id = record["id"]
    data = DailyMaterialService.material_bytes(record)
    mime = record.get("content_type") or "image/png"
    report_model = get_settings().REPORT_MODEL or None
    gateway = LLMGateway(model=report_model, timeout=_DESCRIBE_TIMEOUT)

    try:
        resp = await asyncio.wait_for(
            gateway.complete_multimodal(
                prompt=_IMAGE_PROMPT,
                images=[data],
                image_mime_type=mime,
                max_tokens=1024,
            ),
            timeout=_DESCRIBE_TIMEOUT,
        )
        content = (resp.content or "").strip()
        if content:
            await service.update_analysis(
                material_id,
                desc_status="done",
                ai_description=f"【截图内容识别】{content}",
            )
            logger.info("daily material analyzed: id=%s kind=image ok", material_id)
            return {"material_id": material_id, "status": "done"}
    except Exception as exc:  # noqa: BLE001 - 单张失败仅标记，不阻塞队列
        logger.warning(
            "daily material analyze failed: id=%s error=%s", material_id, exc
        )

    await service.update_analysis(material_id, desc_status="failed")
    return {"material_id": material_id, "status": "failed"}


async def _extract_document(service, record: dict) -> dict:
    """PDF / DOCX 本地提取文本，成功写 extracted_text，失败标 failed。"""
    from app.services.daily_material_service import DailyMaterialService

    material_id = record["id"]
    filename = record.get("filename", "")
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    data = DailyMaterialService.material_bytes(record)

    text = ""
    try:
        if ext == "pdf":
            import io

            from pypdf import PdfReader

            reader = PdfReader(io.BytesIO(data))
            parts = []
            for page in reader.pages[:20]:  # 缓冲区场景只取前 20 页
                parts.append(page.extract_text() or "")
            text = "\n".join(parts)
        elif ext == "docx":
            import io

            import docx

            document = docx.Document(io.BytesIO(data))
            text = "\n".join(p.text for p in document.paragraphs)
        else:
            await service.update_analysis(material_id, desc_status="failed")
            return {"material_id": material_id, "status": "failed"}
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "daily material extract failed: id=%s error=%s", material_id, exc
        )
        await service.update_analysis(material_id, desc_status="failed")
        return {"material_id": material_id, "status": "failed"}

    text = text.strip()[:_MAX_EXTRACT_CHARS]
    if not text:
        await service.update_analysis(material_id, desc_status="failed")
        return {"material_id": material_id, "status": "failed"}

    await service.update_analysis(
        material_id, desc_status="done", extracted_text=text
    )
    logger.info(
        "daily material analyzed: id=%s kind=other chars=%d", material_id, len(text)
    )
    return {"material_id": material_id, "status": "done"}
