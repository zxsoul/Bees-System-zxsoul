"""Daily material buffer service —— 员工端「今日速汇报」的临时材料缓冲区。

设计动机（与长期知识库的边界）：
员工当天随手上传的截图 / 日志 / 文档**只服务于当日日报合成**，属于
临时工作素材，不应该进入文档管线（解析 / 分块 / 向量化 / 双索引）做
长期存储——否则员工每天的临时截图会污染知识库检索结果。

因此本服务用 Redis 实现「缓存式」暂存：

- **与空间完全解耦**：缓冲区只属于「员工 + 日期」，不关联任何空间、
  不做空间权限校验。它是员工个人的临时暂存区，就像桌面上的收件箱，
  材料从哪来、属于哪个项目都不重要，生成日报时按空间合成即可。
- 元数据 + 文件内容（base64）整体作为一个 JSON 字符串存放在 Redis，
  TTL 24 小时，过期自动清空（一天之后材料即失去价值）。
- 每个 (user, date) 一个列表键，记录当日全部材料 ID，
  便于日报合成时一次性取回，也便于 Beat 定时任务扫描「今天谁有材料」。
- 不触碰 PostgreSQL / MinIO / OpenSearch / Qdrant，上传即返回，
  删除即真正消失（误传可删了重传，无残留）。

日期口径：与日报一致，按 UTC 日界划分。
"""

from __future__ import annotations

import base64
import json
import logging
import uuid
from datetime import date, datetime, timezone

from app.core.redis import get_redis

logger = logging.getLogger(__name__)

# Redis key 前缀
_LIST_PREFIX = "wf:daily_materials:list"  # + :{user_id}:{date}
_ITEM_PREFIX = "wf:daily_materials:item"  # + :{material_id}

# 缓冲区 TTL：24 小时（当天有效，隔夜自动清空）
BUFFER_TTL_SECONDS = 24 * 3600

# 单个材料大小上限（截图一般 1-3MB，留足余量）
MAX_MATERIAL_SIZE = 10 * 1024 * 1024
# 每人每天材料数量上限
MAX_MATERIALS_PER_DAY = 50

# 文本类扩展名：内容可直接解码喂给 LLM
TEXT_EXTENSIONS = {
    "txt", "md", "markdown", "log", "csv", "json", "xml", "yaml", "yml",
    "py", "js", "ts", "jsx", "tsx", "java", "go", "rs", "c", "cpp", "h",
    "sql", "sh", "bat", "ps1", "html", "htm", "css", "ini", "conf", "env",
}
# 图片类扩展名：生成日报时走多模态视觉描述
IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "webp", "gif", "bmp"}

IMAGE_CONTENT_TYPES = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "webp": "image/webp",
    "gif": "image/gif",
    "bmp": "image/bmp",
}


def _list_key(user_id: uuid.UUID, day: date) -> str:
    return f"{_LIST_PREFIX}:{user_id}:{day.isoformat()}"


def _item_key(material_id: str) -> str:
    return f"{_ITEM_PREFIX}:{material_id}"


def classify_material(filename: str, content_type: str | None) -> str:
    """根据文件名/Content-Type 判定材料种类：text / image / other。"""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext in IMAGE_EXTENSIONS or (content_type or "").startswith("image/"):
        return "image"
    if ext in TEXT_EXTENSIONS or (content_type or "").startswith("text/"):
        return "text"
    return "other"


class DailyMaterialService:
    """今日材料缓冲区（纯 Redis 个人暂存，无长期存储、无空间绑定）。"""

    # ─── 写入 ─────────────────────────────────────────────────────────

    async def add_material(
        self,
        *,
        user_id: uuid.UUID,
        filename: str,
        content_type: str | None,
        data: bytes,
        day: date | None = None,
    ) -> dict:
        """把一份材料放入我的缓冲区，返回材料元数据（不含内容）。

        Raises:
            ValueError: 超限（单文件过大 / 当日数量超限）
        """
        if not data:
            raise ValueError("文件内容为空")
        if len(data) > MAX_MATERIAL_SIZE:
            raise ValueError(
                f"文件过大：{len(data) / 1024 / 1024:.1f}MB，"
                f"上限 {MAX_MATERIAL_SIZE // 1024 // 1024}MB"
            )

        day = day or datetime.now(timezone.utc).date()
        redis = await get_redis()
        list_key = _list_key(user_id, day)

        count = await redis.llen(list_key)
        if count >= MAX_MATERIALS_PER_DAY:
            raise ValueError(f"当日材料已达上限（{MAX_MATERIALS_PER_DAY} 份）")

        material_id = uuid.uuid4().hex
        kind = classify_material(filename, content_type)
        # 预处理字段：image/other 上传后由 Celery 异步识别（desc_status
        # pending → processing → done/failed），生成日报时直接取用结果，
        # 不再每次生成重复调用视觉模型。text 类本地解码零成本，直接 done。
        record = {
            "id": material_id,
            "user_id": str(user_id),
            "date": day.isoformat(),
            "filename": filename,
            "content_type": content_type or "application/octet-stream",
            "size": len(data),
            "kind": kind,
            "uploaded_at": datetime.now(timezone.utc).isoformat(),
            "content_b64": base64.b64encode(data).decode("ascii"),
            "desc_status": "done" if kind == "text" else "pending",
            "ai_description": None,
            "extracted_text": (
                data.decode("utf-8", errors="replace")[:8000]
                if kind == "text"
                else None
            ),
        }

        await redis.set(
            _item_key(material_id), json.dumps(record), ex=BUFFER_TTL_SECONDS
        )
        await redis.rpush(list_key, material_id)
        await redis.expire(list_key, BUFFER_TTL_SECONDS)

        logger.info(
            "daily material buffered: user=%s file=%s size=%d",
            user_id,
            filename,
            len(data),
        )
        return self._to_meta(record)

    # ─── 查询 ─────────────────────────────────────────────────────────

    async def list_materials(
        self,
        *,
        user_id: uuid.UUID,
        day: date | None = None,
    ) -> list[dict]:
        """列出我当日缓冲区材料（按上传顺序，元数据不含文件内容）。"""
        records = await self._get_records(user_id=user_id, day=day)
        return [self._to_meta(r) for r in records]

    async def list_materials_with_content(
        self,
        *,
        user_id: uuid.UUID,
        day: date | None = None,
    ) -> list[dict]:
        """列出我当日材料（含 base64 内容）——仅供日报合成内部使用。"""
        return await self._get_records(user_id=user_id, day=day)

    async def get_material(self, material_id: str) -> dict | None:
        """取单份材料完整记录（含内容）；不存在或已过期返回 None。"""
        redis = await get_redis()
        raw = await redis.get(_item_key(material_id))
        if not raw:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None

    @staticmethod
    def material_bytes(record: dict) -> bytes:
        """从完整记录还原文件字节。"""
        return base64.b64decode(record.get("content_b64") or b"")

    @staticmethod
    def extract_text(record: dict, max_chars: int = 8000) -> str:
        """从文本类材料中提取可读内容（UTF-8 解码，容错替换）。"""
        data = DailyMaterialService.material_bytes(record)
        return data.decode("utf-8", errors="replace")[:max_chars]

    # ─── 异步识别结果写回 ──────────────────────────────────────────────

    async def update_analysis(
        self,
        material_id: str,
        *,
        desc_status: str,
        ai_description: str | None = None,
        extracted_text: str | None = None,
    ) -> bool:
        """把异步识别结果写回材料记录（image 描述 / other 文本提取）。

        供 Celery 识别任务调用；材料已过期或被删除时返回 False。
        """
        redis = await get_redis()
        key = _item_key(material_id)
        raw = await redis.get(key)
        if not raw:
            return False
        try:
            record = json.loads(raw)
        except json.JSONDecodeError:
            return False

        record["desc_status"] = desc_status
        if ai_description is not None:
            record["ai_description"] = ai_description
        if extracted_text is not None:
            record["extracted_text"] = extracted_text

        # 保留原 TTL：读剩余过期时间再写回，避免识别结果刷新材料寿命
        ttl = await redis.ttl(key)
        await redis.set(key, json.dumps(record), ex=max(ttl, 60))
        return True

    # ─── 删除 ─────────────────────────────────────────────────────────

    async def delete_material(
        self,
        *,
        material_id: str,
        user_id: uuid.UUID,
    ) -> bool:
        """删除一份材料（仅上传者本人）。返回是否删除成功。"""
        record = await self.get_material(material_id)
        if not record:
            return False
        if record["user_id"] != str(user_id):
            return False

        redis = await get_redis()
        await redis.delete(_item_key(material_id))
        # 同步从当日列表中移除（列表过期前仍引用该 ID）
        list_key = _list_key(
            uuid.UUID(record["user_id"]),
            date.fromisoformat(record["date"]),
        )
        await redis.lrem(list_key, 0, material_id)
        logger.info("daily material deleted: id=%s user=%s", material_id, user_id)
        return True

    # ─── Beat 扫描 ────────────────────────────────────────────────────

    async def iter_active_users(self, day: date) -> list[uuid.UUID]:
        """扫描当日缓冲区里有材料的用户 ID（供定时任务用）。"""
        redis = await get_redis()
        pattern = f"{_LIST_PREFIX}:*:{day.isoformat()}"
        users: list[uuid.UUID] = []
        async for key in redis.scan_iter(match=pattern, count=200):
            # key 形如 wf:daily_materials:list:{user_id}:{date}
            parts = key.split(":")
            if len(parts) != 5:
                continue
            try:
                users.append(uuid.UUID(parts[3]))
            except ValueError:
                continue
        return users

    # ─── 内部 ─────────────────────────────────────────────────────────

    async def _get_records(
        self,
        *,
        user_id: uuid.UUID,
        day: date | None,
    ) -> list[dict]:
        day = day or datetime.now(timezone.utc).date()
        redis = await get_redis()
        ids = await redis.lrange(_list_key(user_id, day), 0, -1)
        if not ids:
            return []
        records: list[dict] = []
        for material_id in ids:
            raw = await redis.get(_item_key(material_id))
            if not raw:
                continue  # 单项已过期，跳过
            try:
                records.append(json.loads(raw))
            except json.JSONDecodeError:
                continue
        return records

    @staticmethod
    def _to_meta(record: dict) -> dict:
        """剥离文件内容，只保留对外展示的元数据。"""
        return {k: v for k, v in record.items() if k != "content_b64"}
