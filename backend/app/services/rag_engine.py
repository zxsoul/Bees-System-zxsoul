"""RAG Engine: Retrieval-Augmented Generation conversational Q&A.

Implements:
- RAGEngine.chat(): Retrieve top-K chunks → build prompt → stream LLM response
- Session management via Redis (last 20 turns, sliding TTL 7 days)
- Citation parsing from LLM output ([1], [2] format)
- Similarity threshold filtering (default 0.5)
- LLM timeout handling (default 60s)
- Query enhancement (rewrite / HyDE / sub-query decomposition) wired into retrieval
- Question contextualization with conversation history + current date
- Graceful fallback: answers from general knowledge when no chunks match
"""

import asyncio
import json
import logging
import re
import time
import uuid
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import func, select

from app.services.llm_gateway import LLMGateway, LLMGatewayError
from app.services.query_enhancer import QueryEnhancer, build_query_enhancer
from app.services.search_service import SearchHit, SearchService

logger = logging.getLogger(__name__)

# Constants
DEFAULT_TOP_K = 5
MIN_TOP_K = 1
MAX_TOP_K = 20
DEFAULT_SIMILARITY_THRESHOLD = 0.5
DEFAULT_LLM_TIMEOUT = 60.0
# 会话滑动过期时间：7 天。每次保存对话轮次都会刷新 TTL，连续使用不过期。
SESSION_TTL_SECONDS = 7 * 24 * 3600
MAX_CONVERSATION_TURNS = 20
NO_RELEVANT_INFO_MESSAGE = "未找到相关信息，无法回答您的问题。请尝试换一种方式提问。"
LLM_ERROR_MESSAGE = "服务暂时不可用，请稍后重试。"

# 检索前的问题语境化：把依赖对话上下文/相对时间的问题改写为独立检索问题。
# 注意：思考型模型（kimi-k2.6）一次完整生成约 10s+，且只接受 temperature=1，
# max_tokens 需要给足以容纳推理 token。
_CONTEXTUALIZE_TIMEOUT = 15.0
_CONTEXTUALIZE_SYSTEM_PROMPT = (
    "你是一个检索问题改写助手。给你一段对话历史和用户的最新问题，请把最新问题"
    "改写成一个独立的、自包含的搜索问题，使它在没有对话上下文时也能被准确理解。\n\n"
    "改写要求：\n"
    "1. 把「今天」「昨天」「本周」等相对时间替换为具体日期（当前日期会在用户消息中给出）。\n"
    "2. 补全指代：如「它」「该部门」「之前的方案」等，根据对话历史替换为具体对象。\n"
    "3. 保持原问题的意图和范围，不要回答问题本身，不要添加额外信息。\n"
    "4. 只输出改写后的问题文本，不要输出任何解释、引号或前后缀。\n"
    "5. 如果原问题本身已经独立完整、不依赖对话历史，请原样输出。"
)

# Redis key prefix
SESSION_KEY_PREFIX = "session:"


@dataclass
class RAGConfig:
    """Configuration for a RAG chat request.

    Attributes:
        top_k: Number of top chunks to retrieve (1-20, default 5)
        similarity_threshold: Minimum similarity score (0-1, default 0.5)
        llm_timeout: LLM call timeout in seconds (default 60)
        model: LLM model to use (defaults to settings)
        temperature: LLM temperature (0-2, default 0.1)
        max_tokens: Maximum tokens for LLM response (default 4096)
    """

    top_k: int = DEFAULT_TOP_K
    similarity_threshold: float = DEFAULT_SIMILARITY_THRESHOLD
    llm_timeout: float = DEFAULT_LLM_TIMEOUT
    model: str | None = None
    temperature: float = 0.1
    max_tokens: int = 4096


@dataclass
class Citation:
    """A citation reference in the RAG response.

    Attributes:
        index: Citation number (1-based)
        document_id: Source document ID
        chunk_id: Source chunk ID
        source_file: Source file name
        title_chain: Title chain (e.g., "Chapter 1 > Section 2")
        chunk_index: Position index of the chunk in the document
    """

    index: int
    document_id: str
    chunk_id: str
    source_file: str
    title_chain: str
    chunk_index: int


@dataclass
class RAGResponse:
    """Complete RAG response after streaming is done.

    Attributes:
        content: Full generated text
        citations: List of citations referenced in the response
        session_id: Session ID for this conversation
    """

    content: str = ""
    citations: list[Citation] = field(default_factory=list)
    session_id: str = ""


class RAGEngine:
    """RAG conversational Q&A engine.

    Orchestrates:
    1. Retrieve relevant document chunks via SearchService
    2. Build prompt with context + conversation history
    3. Stream LLM response via LLMGateway
    4. Parse citations from LLM output
    5. Manage conversation sessions in Redis
    """

    def __init__(
        self,
        search_service: SearchService | None = None,
        llm_gateway: LLMGateway | None = None,
        redis_client=None,
        query_enhancer: QueryEnhancer | None = None,
    ):
        """Initialize the RAG engine.

        Args:
            search_service: Service for retrieving relevant chunks.
            llm_gateway: Gateway for LLM calls.
            redis_client: Redis client for session management.
            query_enhancer: 查询增强器（改写 / HyDE / 子查询分解）。
                ``None`` 时首次使用前按 Settings 开关惰性构造。
        """
        self._search_service = search_service or SearchService()
        self._llm_gateway = llm_gateway
        self._redis = redis_client
        self._query_enhancer = query_enhancer
        # 最近一次 chat() 调用的结果元信息（供 SSE 层在流结束后读取）：
        # 引擎实例由 get_rag_engine 按请求创建，不存在并发串扰。
        self.last_citations: list[dict] = []
        self.last_session_id: str | None = None

    def _get_query_enhancer(self) -> QueryEnhancer:
        """惰性构造 QueryEnhancer（开关由 Settings 的 QUERY_ENHANCEMENT_* 驱动）。"""
        if self._query_enhancer is None:
            self._query_enhancer = build_query_enhancer()
        return self._query_enhancer

    def _get_llm_gateway(self, config: RAGConfig) -> LLMGateway:
        """Get or create LLM gateway with config-specific settings."""
        if self._llm_gateway:
            return self._llm_gateway
        return LLMGateway(
            model=config.model,
            timeout=config.llm_timeout,
        )

    async def _get_redis(self):
        """Get Redis client (lazy initialization)."""
        if self._redis is None:
            from app.core.redis import get_redis
            self._redis = await get_redis()
        return self._redis

    # ─── Main Chat Entry Point ─────────────────────────────────────────

    async def chat(
        self,
        question: str,
        session_id: str | None,
        user_id: str,
        allowed_space_ids: list[str],
        config: RAGConfig | None = None,
    ) -> AsyncGenerator[str, None]:
        """Stream a RAG response for the given question.

        Flow:
        1. Load conversation history from Redis
        2. Contextualize question against history + current date
        3. Query enhancement (rewrite / HyDE / sub-queries) via QueryEnhancer
        4. Retrieve top-K chunks (main query + enhanced supplements)
        5. Filter by similarity threshold
        6. Build prompt (knowledge-base mode or general-knowledge fallback)
        7. Stream LLM response
        8. Save conversation turn to Redis

        Args:
            question: User's question
            session_id: Existing session ID (None to create new)
            user_id: Current user's ID
            allowed_space_ids: Spaces the user can access
            config: RAG configuration

        Yields:
            Tokens/chunks of the LLM response as they arrive

        Raises:
            LLMGatewayError: If LLM call fails or times out
        """
        start_time = time.perf_counter()
        if config is None:
            config = RAGConfig()

        # Clamp top_k to valid range
        config.top_k = max(MIN_TOP_K, min(MAX_TOP_K, config.top_k))

        # Create or validate session
        if not session_id:
            session_id = str(uuid.uuid4())
            logger.info(
                "rag chat session created: user_id=%s session_id=%s",
                user_id,
                session_id,
            )
        else:
            # 持久化会话：Redis 热缓存 miss 时尝试从 Postgres 恢复历史并回填，
            # 恢复失败（PG 中也不存在）则沿用该 ID 作为新会话开始，
            # 不再因为缓存过期而静默轮换 session_id。
            if not await self._redis_session_exists(session_id):
                restored = await self._restore_session_from_db(session_id)
                logger.info(
                    "rag chat session cache miss: user_id=%s session_id=%s restored_from_db=%s",
                    user_id,
                    session_id,
                    restored,
                )

        # 记录到实例属性，供 SSE 层在流结束后回传给前端
        self.last_session_id = session_id

        # 1. Load conversation history first (needed for question contextualization)
        history = await self._get_conversation_history(session_id)
        logger.info(
            "rag history loaded: user_id=%s session_id=%s messages=%d",
            user_id,
            session_id,
            len(history),
        )

        # 2. Contextualize the question: resolve relative dates ("今天") and
        # coreferences ("之前的方案") against history so retrieval sees a
        # self-contained query. Falls back to the raw question on any failure.
        retrieval_question = await self._contextualize_question(question, history)

        # 3. Query enhancement (任务 15.x 设计接线): rewrite variants + HyDE
        # embeddings + sub-query decomposition, parallel with 5s overall timeout.
        # On timeout/failure the enhancer degrades to the original query only.
        enhanced = await self._get_query_enhancer().enhance(retrieval_question)
        extra_queries = [
            q for q in enhanced.all_text_queries if q != retrieval_question
        ]
        logger.info(
            "rag query enhanced: user_id=%s session_id=%s variants=%d sub_queries=%d hyde=%d",
            user_id,
            session_id,
            len(enhanced.variants),
            len(enhanced.sub_queries),
            len(enhanced.hyde_embeddings),
        )

        # 4. Retrieve relevant chunks (main query + enhancement supplements)
        chunks = await self._retrieve_chunks(
            question=retrieval_question,
            user_id=user_id,
            allowed_space_ids=allowed_space_ids,
            top_k=config.top_k,
            extra_queries=extra_queries,
            extra_dense_vectors=enhanced.hyde_embeddings,
        )
        logger.info(
            "rag retrieval completed: user_id=%s session_id=%s chunks=%d top_k=%d",
            user_id,
            session_id,
            len(chunks),
            config.top_k,
        )

        # 5. Filter by similarity threshold
        relevant_chunks = [
            c for c in chunks if c.score >= config.similarity_threshold
        ]
        logger.info(
            "rag relevance filtered: user_id=%s session_id=%s relevant_chunks=%d threshold=%.3f",
            user_id,
            session_id,
            len(relevant_chunks),
            config.similarity_threshold,
        )

        # 6. Build prompt. 没有检索到达标资料时不再硬拒绝，而是切换为通用
        # 问答模式：照常调用 LLM，由系统提示词要求模型显式标注回答并非来自
        # 知识库，兼顾常识类问题（日期、计算、闲聊）的可用性。
        prompt, system_prompt = self._build_prompt(
            question, relevant_chunks, history
        )
        logger.info(
            "rag prompt built: user_id=%s session_id=%s prompt_len=%d context_chunks=%d",
            user_id,
            session_id,
            len(prompt),
            len(relevant_chunks),
        )

        # 6. Stream LLM response
        llm = self._get_llm_gateway(config)
        full_response = ""

        try:
            async for token in llm.stream(
                prompt=prompt,
                system_prompt=system_prompt,
                temperature=config.temperature,
                max_tokens=config.max_tokens,
            ):
                full_response += token
                yield token
        except LLMGatewayError as e:
            if e.reason == "timeout":
                error_msg = LLM_ERROR_MESSAGE
                # Preserve session context on error
                await self._save_turn(session_id, user_id, question, error_msg, [])
                yield error_msg
                return
            raise

        # 7. Parse citations from response
        citations = self._parse_citations(full_response, relevant_chunks)
        logger.info(
            "rag llm stream completed: user_id=%s session_id=%s response_len=%d "
            "citations=%d elapsed_ms=%d",
            user_id,
            session_id,
            len(full_response),
            len(citations),
            int((time.perf_counter() - start_time) * 1000),
        )

        # 8. Save conversation turn
        citation_dicts = [self._citation_to_dict(c) for c in citations]
        await self._save_turn(
            session_id, user_id, question, full_response, citation_dicts
        )
        # 记录到实例属性，供 SSE 层在流结束后回传给前端
        self.last_citations = citation_dicts
        logger.info(
            "rag turn saved: user_id=%s session_id=%s citations=%d",
            user_id,
            session_id,
            len(citations),
        )

    # ─── Retrieve Chunks ───────────────────────────────────────────────

    async def _retrieve_chunks(
        self,
        question: str,
        user_id: str,
        allowed_space_ids: list[str],
        top_k: int,
        extra_queries: list[str] | None = None,
        extra_dense_vectors: list[list[float]] | None = None,
    ) -> list[SearchHit]:
        """Retrieve top-K relevant chunks using the search service.

        Args:
            question: 检索用问题（语境化后的独立问题）
            user_id: Current user's ID
            allowed_space_ids: Accessible space IDs
            top_k: Number of chunks to retrieve
            extra_queries: 查询增强产出的改写变体 + 子查询
            extra_dense_vectors: HyDE 假设文档向量

        Returns:
            List of SearchHit results
        """
        response = await self._search_service.search(
            query=question,
            user_id=user_id,
            allowed_space_ids=allowed_space_ids,
            page=1,
            page_size=top_k,
            extra_queries=extra_queries,
            extra_dense_vectors=extra_dense_vectors,
        )
        logger.info(
            "rag search response received: user_id=%s total=%d returned=%d",
            user_id,
            response.total,
            len(response.results),
        )

        # Convert SearchResult back to SearchHit for internal use
        hits = []
        for r in response.results:
            hits.append(SearchHit(
                chunk_id=r.chunk_id,
                document_id=r.document_id,
                chunk_index=r.chunk_index,
                title_chain=r.title_chain,
                source_file=r.source_file,
                content=r.content or r.highlight,  # 完整 chunk 正文，缺失时降级为高亮片段
                score=r.score,
            ))
        return hits

    # ─── Question Contextualization ────────────────────────────────────

    async def _contextualize_question(
        self, question: str, history: list[dict]
    ) -> str:
        """把依赖上下文的问题改写为独立检索问题（指代消解 + 相对日期落地）。

        仅在存在对话历史时执行；任何超时 / LLM 异常都静默降级为原问题，
        不影响主链路。当前日期同时提供给改写模型，保证「今天」「昨天」等
        相对时间被替换为具体日期。
        """
        if not history or not question.strip():
            return question

        today = datetime.now().strftime("%Y-%m-%d %A")
        # 只取最近 6 条消息控制 prompt 长度
        recent = history[-6:]
        history_lines = []
        for turn in recent:
            role = "用户" if turn.get("role") == "user" else "助手"
            history_lines.append(f"{role}: {(turn.get('content') or '')[:200]}")
        history_text = "\n".join(history_lines)

        prompt = (
            f"当前日期：{today}\n\n"
            f"对话历史：\n{history_text}\n\n"
            f"用户最新问题：{question}\n\n"
            "请输出改写后的独立搜索问题："
        )

        try:
            from app.core.config import get_settings

            fast_model = get_settings().FAST_CHAT_MODEL or None
            llm = LLMGateway(model=fast_model, timeout=_CONTEXTUALIZE_TIMEOUT)
            # 不传 temperature（模型只接受 temperature=1，传其它值会触发
            # 400 降级重试）；max_tokens 给足以容纳推理 token。
            # 配置了 FAST_CHAT_MODEL 时走快速模型，语境化延迟从 ~12s 降到 ~2s。
            response = await asyncio.wait_for(
                llm.complete(
                    prompt=prompt,
                    system_prompt=_CONTEXTUALIZE_SYSTEM_PROMPT,
                    max_tokens=2048,
                ),
                timeout=_CONTEXTUALIZE_TIMEOUT,
            )
            rewritten = (response.content or "").strip().strip('"').strip("'")
            #  sanity check：空输出或异常超长都视为改写失败
            if not rewritten or len(rewritten) > 500:
                return question
            if rewritten != question.strip():
                logger.info(
                    "rag question contextualized: origin_len=%d rewritten_len=%d",
                    len(question),
                    len(rewritten),
                )
            return rewritten
        except Exception as e:  # noqa: BLE001 - 静默降级，检索仍用原问题
            logger.warning("rag contextualize failed, fallback to origin: %s", e)
            return question

    # ─── Prompt Building ───────────────────────────────────────────────

    def _build_prompt(
        self,
        question: str,
        chunks: list[SearchHit],
        history: list[dict],
    ) -> tuple[str, str]:
        """Build the prompt with context and conversation history.

        The system prompt instructs the LLM to:
        - Answer based on provided context
        - Use [1], [2] citation format
        - Respond in the same language as the question

        当前日期注入系统提示词，让模型能正确理解「今天」「本周」等
        相对时间表述。无检索资料时切换为通用问答模式，要求模型显式
        标注回答并非来自知识库。

        Args:
            question: User's current question
            chunks: Retrieved relevant chunks (may be empty)
            history: Previous conversation turns

        Returns:
            Tuple of (user_prompt, system_prompt)
        """
        today = datetime.now().strftime("%Y-%m-%d %A")

        if chunks:
            system_prompt = (
                "你是一个企业知识库助手。请根据提供的参考资料回答用户的问题。\n\n"
                f"当前日期：{today}。用户提到「今天」「昨天」「本周」等相对时间时，"
                "请以此日期为基准理解。\n\n"
                "规则：\n"
                "1. 仅基于提供的参考资料回答，不要编造信息。\n"
                "2. 在回答中使用 [1]、[2] 等格式标注引用来源，对应参考资料的编号。\n"
                "3. 如果参考资料不足以回答问题，请明确说明。\n"
                "4. 使用与用户问题相同的语言回答。\n"
                "5. 完整覆盖参考资料中与问题相关的所有要点，不要遗漏；"
                "在此前提下保持简洁、准确、有条理。"
            )
        else:
            system_prompt = (
                "你是一个企业知识库助手。本次提问没有从知识库中检索到相关参考资料。\n\n"
                f"当前日期：{today}。用户提到「今天」「昨天」「本周」等相对时间时，"
                "请以此日期为基准理解。\n\n"
                "规则：\n"
                "1. 如果问题属于常识、计算、日期、通用知识或闲聊，请基于你的通用知识"
                "直接回答，并在回答末尾注明「（以上回答基于通用知识，非来自知识库）」。\n"
                "2. 如果问题明显在询问企业内部资料（如项目进度、工作日报、文档内容），"
                "请说明知识库中未找到相关内容，并建议用户确认资料是否已上传或换个问法。\n"
                "3. 使用与用户问题相同的语言回答。\n"
                "4. 回答要简洁、准确、有条理。"
            )

        # Build context section
        context_parts = []
        for i, chunk in enumerate(chunks, start=1):
            source_info = f"[来源: {chunk.source_file}"
            if chunk.title_chain:
                source_info += f" > {chunk.title_chain}"
            source_info += "]"
            context_parts.append(f"[{i}] {source_info}\n{chunk.content}")

        context_section = "\n\n".join(context_parts)

        # Build conversation history section
        history_section = ""
        if history:
            history_lines = []
            for turn in history:
                role = turn.get("role", "")
                content = turn.get("content", "")
                if role == "user":
                    history_lines.append(f"用户: {content}")
                elif role == "assistant":
                    history_lines.append(f"助手: {content}")
            history_section = "\n".join(history_lines)

        # Compose user prompt
        user_prompt_parts = []
        if chunks:
            user_prompt_parts.append(f"参考资料：\n{context_section}")
        if history_section:
            user_prompt_parts.append(f"\n对话历史：\n{history_section}")
        user_prompt_parts.append(f"\n当前问题：{question}")

        user_prompt = "\n".join(user_prompt_parts)

        return user_prompt, system_prompt

    # ─── Citation Parsing ──────────────────────────────────────────────

    def _parse_citations(
        self, response_text: str, chunks: list[SearchHit]
    ) -> list[Citation]:
        """Parse citation references from LLM response text.

        Looks for [1], [2], etc. patterns and maps them to source chunks.

        Args:
            response_text: Full LLM response text
            chunks: The chunks that were provided as context

        Returns:
            List of Citation objects for referenced chunks
        """
        # Find all citation numbers in the response
        citation_pattern = re.compile(r"\[(\d+)\]")
        matches = citation_pattern.findall(response_text)

        # Deduplicate and sort
        cited_indices = sorted(set(int(m) for m in matches))

        citations = []
        for idx in cited_indices:
            # Citation indices are 1-based, chunks list is 0-based
            chunk_pos = idx - 1
            if 0 <= chunk_pos < len(chunks):
                chunk = chunks[chunk_pos]
                citations.append(Citation(
                    index=idx,
                    document_id=chunk.document_id,
                    chunk_id=chunk.chunk_id,
                    source_file=chunk.source_file,
                    title_chain=chunk.title_chain,
                    chunk_index=chunk.chunk_index,
                ))

        return citations

    def _citation_to_dict(self, citation: Citation) -> dict:
        """Convert a Citation to a JSON-serializable dict."""
        return {
            "index": citation.index,
            "document_id": citation.document_id,
            "chunk_id": citation.chunk_id,
            "source_file": citation.source_file,
            # 前端 Citation 类型使用 document_title 字段展示文件名
            "document_title": citation.source_file,
            "title_chain": citation.title_chain,
            "chunk_index": citation.chunk_index,
        }

    # ─── Session Management ────────────────────────────────────────────

    async def _get_session_key(self, session_id: str) -> str:
        """Get the Redis key for a session."""
        return f"{SESSION_KEY_PREFIX}{session_id}"

    async def _get_conversation_history(self, session_id: str) -> list[dict]:
        """Get conversation history (Redis first, Postgres as source of truth).

        Returns the last 20 turns (messages) for the session. Redis 热缓存
        miss 时从 chat_messages 表恢复并回填缓存。

        Args:
            session_id: Session ID

        Returns:
            List of message dicts with 'role' and 'content' keys
        """
        redis = await self._get_redis()
        key = await self._get_session_key(session_id)

        messages_json = await redis.hget(key, "messages")
        if not messages_json:
            # 缓存 miss：从持久层恢复
            try:
                return await self._load_history_from_db(session_id)
            except Exception as e:  # noqa: BLE001
                logger.error(
                    "rag load history from db failed: session_id=%s error=%s",
                    session_id,
                    e,
                )
                return []

        try:
            messages = json.loads(messages_json)
            # Return last 20 turns (each turn = user + assistant = 2 messages)
            # Max 20 turns = 40 messages
            max_messages = MAX_CONVERSATION_TURNS * 2
            return messages[-max_messages:]
        except (json.JSONDecodeError, TypeError):
            return []

    async def _save_turn(
            self,
            session_id: str,
            user_id: str,
            question: str,
            answer: str,
            citations: list[dict],
    ) -> None:
        """Save a conversation turn to Redis.

        Appends user question and assistant answer to the session's message list.
        Maintains max 20 turns (40 messages). Refreshes the sliding TTL (7 days).

        Args:
            session_id: Session ID
            user_id: User ID
            question: User's question
            answer: Assistant's answer
            citations: Citation data for the answer
        """
        redis = await self._get_redis()
        key = await self._get_session_key(session_id)

        # Get existing messages
        messages_json = await redis.hget(key, "messages")
        if messages_json:
            try:
                messages = json.loads(messages_json)
            except (json.JSONDecodeError, TypeError):
                messages = []
        else:
            messages = []

        # Append new turn
        messages.append({"role": "user", "content": question})
        messages.append({
            "role": "assistant",
            "content": answer,
            "citations": citations,
        })

        # Trim to max 20 turns (40 messages)
        max_messages = MAX_CONVERSATION_TURNS * 2
        if len(messages) > max_messages:
            messages = messages[-max_messages:]

        # Save to Redis
        now = time.time()
        await redis.hset(key, mapping={
            "user_id": user_id,
            "messages": json.dumps(messages, ensure_ascii=False),
            "last_active": str(now),
            "session_id": session_id,
        })

        # Set sliding TTL (7 days, refreshed on every turn)
        await redis.expire(key, SESSION_TTL_SECONDS)

        # 持久化到 Postgres（chat_sessions / chat_messages）：Redis 只是热缓存，
        # PG 才是会话的真实存档。PG 写失败不影响主流程，仅记录日志。
        try:
            await self._persist_turn_db(
                session_id, user_id, question, answer, citations
            )
        except Exception as e:  # noqa: BLE001 - 持久化失败不应中断聊天
            logger.error(
                "rag persist turn to db failed: session_id=%s error=%s",
                session_id,
                e,
                exc_info=True,
            )

    # ─── Postgres Persistence ──────────────────────────────────────────

    async def _persist_turn_db(
        self,
        session_id: str,
        user_id: str,
        question: str,
        answer: str,
        citations: list[dict],
    ) -> None:
        """把一轮对话写入 chat_sessions / chat_messages（upsert 语义）。"""
        from app.core.database import AsyncSessionLocal
        from app.models.chat import ChatMessage, ChatSession

        sid = uuid.UUID(session_id)
        uid = uuid.UUID(user_id)
        now = datetime.now(timezone.utc)

        async with AsyncSessionLocal() as db:
            session = await db.get(ChatSession, sid)
            if session is None:
                session = ChatSession(
                    id=sid, user_id=uid, last_active_at=now, is_expired=False
                )
                db.add(session)
                next_seq = 1
            else:
                session.last_active_at = now
                result = await db.execute(
                    select(func.max(ChatMessage.sequence_number)).where(
                        ChatMessage.session_id == sid
                    )
                )
                next_seq = (result.scalar() or 0) + 1

            db.add(
                ChatMessage(
                    session_id=sid,
                    role="user",
                    content=question,
                    sequence_number=next_seq,
                )
            )
            db.add(
                ChatMessage(
                    session_id=sid,
                    role="assistant",
                    content=answer,
                    citations=citations or None,
                    sequence_number=next_seq + 1,
                )
            )
            await db.commit()

    async def _load_history_from_db(self, session_id: str) -> list[dict]:
        """从 Postgres 读取会话最近 20 轮消息（Redis 缓存 miss 时兜底）。"""
        from app.core.database import AsyncSessionLocal
        from app.models.chat import ChatMessage

        sid = uuid.UUID(session_id)
        max_messages = MAX_CONVERSATION_TURNS * 2

        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(ChatMessage)
                .where(ChatMessage.session_id == sid)
                .order_by(ChatMessage.sequence_number.desc())
                .limit(max_messages)
            )
            rows = list(result.scalars().all())

        rows.reverse()
        return [
            {
                "role": r.role,
                "content": r.content,
                "citations": r.citations,
            }
            for r in rows
        ]

    async def _redis_session_exists(self, session_id: str) -> bool:
        """Redis 热缓存中是否存在该会话。"""
        redis = await self._get_redis()
        key = await self._get_session_key(session_id)
        return bool(await redis.exists(key))

    async def _restore_session_from_db(self, session_id: str) -> bool:
        """Redis miss 时从 Postgres 恢复会话历史并回填热缓存。"""
        try:
            messages = await self._load_history_from_db(session_id)
        except Exception as e:  # noqa: BLE001
            logger.error(
                "rag restore session from db failed: session_id=%s error=%s",
                session_id,
                e,
            )
            return False
        if not messages:
            return False

        from app.core.database import AsyncSessionLocal
        from app.models.chat import ChatSession

        # 取会话归属与活跃时间，用于回填缓存元数据
        async with AsyncSessionLocal() as db:
            session = await db.get(ChatSession, uuid.UUID(session_id))
        if session is None:
            return False

        redis = await self._get_redis()
        key = await self._get_session_key(session_id)
        await redis.hset(
            key,
            mapping={
                "user_id": str(session.user_id),
                "messages": json.dumps(messages, ensure_ascii=False),
                "last_active": str(session.last_active_at.timestamp()),
                "session_id": session_id,
            },
        )
        await redis.expire(key, SESSION_TTL_SECONDS)
        logger.info(
            "rag session restored from db: session_id=%s messages=%d",
            session_id,
            len(messages),
        )
        return True

    # ─── Session Queries ───────────────────────────────────────────────

    async def get_session(self, session_id: str) -> dict | None:
        """Get session data (Redis first, Postgres fallback).

        Args:
            session_id: Session ID

        Returns:
            Session data dict or None if not found
        """
        redis = await self._get_redis()
        key = await self._get_session_key(session_id)

        session_data = await redis.hgetall(key)
        if session_data:
            return {
                "session_id": session_id,
                "user_id": session_data.get("user_id", ""),
                "last_active": float(session_data.get("last_active", 0)),
                "is_expired": False,
            }

        # 缓存 miss：查持久层（非法 UUID 直接视为不存在）
        from app.core.database import AsyncSessionLocal
        from app.models.chat import ChatSession

        try:
            sid = uuid.UUID(session_id)
        except ValueError:
            return None
        async with AsyncSessionLocal() as db:
            session = await db.get(ChatSession, sid)
        if session is None:
            return None
        return {
            "session_id": session_id,
            "user_id": str(session.user_id),
            "last_active": session.last_active_at.timestamp(),
            "is_expired": False,
        }

    async def get_user_sessions(self, user_id: str) -> list[dict]:
        """Get sessions for a user from Postgres (持久层，最近 50 个).

        Redis 只是热缓存，会话列表以 chat_sessions 表为准；
        PG 查询失败时降级为扫描 Redis 缓存。

        Args:
            user_id: User ID

        Returns:
            List of session data dicts
        """
        from app.core.database import AsyncSessionLocal
        from app.models.chat import ChatSession

        try:
            async with AsyncSessionLocal() as db:
                result = await db.execute(
                    select(ChatSession)
                    .where(ChatSession.user_id == uuid.UUID(user_id))
                    .order_by(ChatSession.last_active_at.desc())
                    .limit(50)
                )
                rows = result.scalars().all()
            return [
                {
                    "session_id": str(s.id),
                    "user_id": user_id,
                    "last_active": s.last_active_at.timestamp(),
                    "is_expired": False,
                }
                for s in rows
            ]
        except Exception as e:  # noqa: BLE001 - 降级到 Redis 扫描
            logger.error("rag list sessions from db failed, fallback to redis: %s", e)

        redis = await self._get_redis()
        sessions = []
        async for key in redis.scan_iter(match=f"{SESSION_KEY_PREFIX}*"):
            session_data = await redis.hgetall(key)
            if session_data.get("user_id") == user_id:
                session_id = session_data.get("session_id", key.replace(SESSION_KEY_PREFIX, ""))
                sessions.append({
                    "session_id": session_id,
                    "user_id": user_id,
                    "last_active": float(session_data.get("last_active", 0)),
                    "is_expired": False,
                })
        return sessions

    async def get_session_history(self, session_id: str) -> list[dict]:
        """Get full message history for a session.

        Args:
            session_id: Session ID

        Returns:
            List of message dicts
        """
        return await self._get_conversation_history(session_id)

    async def delete_session(self, session_id: str) -> bool:
        """删除一个会话：Postgres 持久记录（级联删消息）+ Redis 缓存。

        Args:
            session_id: Session ID

        Returns:
            True 表示有删除发生, False 表示会话不存在
        """
        from app.core.database import AsyncSessionLocal
        from app.models.chat import ChatSession

        deleted = False
        try:
            sid = uuid.UUID(session_id)
        except ValueError:
            sid = None
        if sid is not None:
            try:
                async with AsyncSessionLocal() as db:
                    session = await db.get(ChatSession, sid)
                    if session is not None:
                        await db.delete(session)  # cascade 删除 chat_messages
                        await db.commit()
                        deleted = True
            except Exception as e:  # noqa: BLE001
                logger.error(
                    "rag delete session from db failed: session_id=%s error=%s",
                    session_id,
                    e,
                )

        redis = await self._get_redis()
        key = await self._get_session_key(session_id)
        deleted = bool(await redis.delete(key)) or deleted
        return deleted
