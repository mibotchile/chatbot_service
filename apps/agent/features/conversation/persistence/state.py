"""Conversation state with PostgreSQL persistence.

When a db_pool (asyncpg.Pool) is provided with a schema name, messages and
record data are persisted to the conversations table automatically.  Without it,
everything stays in-memory -- this is the default and keeps existing tests
working without changes.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from loguru import logger

from features.conversation.hooks import extract_implicit_data
from features.conversation.record import Record
from shared.ports.capture_spec import CaptureSpec

if TYPE_CHECKING:
    import asyncpg


class ConversationState:
    """State for a single conversation."""

    def __init__(
        self,
        conversation_id: str,
        *,
        db_pool: asyncpg.Pool | None = None,
        db_schema: str = "dev",
        visitor_id: str | None = None,
        history: list[dict] | None = None,
        record_data: dict | None = None,
        capture_spec: CaptureSpec | None = None,
    ):
        self.conversation_id = conversation_id
        self.db_pool = db_pool
        self.db_schema = db_schema
        self.visitor_id = visitor_id
        self.history: list[dict] = list(history) if history else []
        if capture_spec is None:
            raise ValueError(
                "capture_spec is required — pass a CaptureSpec from the composition root"
            )
        self.debtor = Record(spec=capture_spec, initial_data=record_data)
        self.page_context: dict = {}
        self.brochures_sent: set[str] = set()  # project slugs already emailed
        self.debtor_notified: bool = False  # sales team already notified
        # Identity gate (cobranza): resolved server-side from the campaign token.
        # debt_context holds the verified borrower profile (incl. account_id).
        self.identity_verified: bool = False
        self.debt_context: dict = {}
        # Per-session scratch for the curated-responses engine: variant no-repeat
        # memory + the chosen credit for desambiguación (multi-credit borrowers).
        self.session_state: dict = {}

    # -- Sync API (in-memory only, backwards-compatible) --

    def add_user_message(self, text: str) -> None:
        self.history.append({"role": "user", "content": text})
        extracted = extract_implicit_data(text)
        if extracted:
            self.debtor.update(extracted)

    def add_assistant_message(self, content: str) -> None:
        self.history.append({"role": "assistant", "content": content})

    # -- Async API (persists when db_pool is set) --

    async def add_user_message_async(self, text: str) -> None:
        self.add_user_message(text)
        await self._persist()

    async def add_assistant_message_async(self, content: str) -> None:
        self.add_assistant_message(content)
        await self._persist()

    async def _persist(self) -> None:
        """Save full conversation state to DB if pool is available."""
        if self.db_pool is None:
            return
        try:
            from shared.persistence.persistence import save_conversation

            await save_conversation(
                self.db_pool,
                self.db_schema,
                self.conversation_id,
                visitor_id=self.visitor_id,
                history=self.history,
                record_data=self.debtor.collected,
                record_level=self.debtor.level,
                page_context=self.page_context,
            )
        except Exception:
            logger.opt(exception=True).warning(
                "Failed to persist conversation {}", self.conversation_id
            )


class StateStore:
    """Store for conversation states with optional DB persistence."""

    def __init__(
        self,
        db_pool: asyncpg.Pool | None = None,
        db_schema: str = "dev",
        capture_spec: CaptureSpec | None = None,
    ):
        self._conversations: dict[str, ConversationState] = {}
        self.db_pool = db_pool
        self.db_schema = db_schema
        self._capture_spec = capture_spec  # always required — composition root provides it

    # -- Sync API (in-memory, backwards-compatible) --

    def get_or_create(self, conversation_id: str | None) -> ConversationState:
        if not conversation_id:
            conversation_id = str(uuid.uuid4())
        if conversation_id not in self._conversations:
            self._conversations[conversation_id] = ConversationState(
                conversation_id,
                db_pool=self.db_pool,
                db_schema=self.db_schema,
                capture_spec=self._capture_spec,
            )
        return self._conversations[conversation_id]

    # -- Async API (loads from DB when pool is available) --

    async def get_or_create_async(
        self,
        conversation_id: str | None,
        visitor_id: str | None = None,
    ) -> ConversationState:
        if not conversation_id:
            conversation_id = str(uuid.uuid4())

        if conversation_id not in self._conversations:
            history: list[dict] = []
            record_data: dict = {}
            page_context: dict = {}

            # Try loading from DB
            if self.db_pool is not None:
                try:
                    from shared.persistence.persistence import load_conversation

                    row = await load_conversation(
                        self.db_pool, self.db_schema, conversation_id
                    )
                    if row:
                        history = row.get("history") or []
                        record_data = row.get("record_data") or {}
                        page_context = row.get("page_context") or {}
                        visitor_id = visitor_id or row.get("visitor_id")
                except Exception:
                    logger.opt(exception=True).warning(
                        "Failed to load conversation {} from DB, starting fresh",
                        conversation_id,
                    )

            conv = ConversationState(
                conversation_id,
                db_pool=self.db_pool,
                db_schema=self.db_schema,
                visitor_id=visitor_id,
                history=history,
                record_data=record_data,
                capture_spec=self._capture_spec,
            )
            conv.page_context = page_context
            self._conversations[conversation_id] = conv

        return self._conversations[conversation_id]


def get_store(
    redis_url: str | None = None,
    db_pool: asyncpg.Pool | None = None,
    db_schema: str = "dev",
    capture_spec: CaptureSpec | None = None,
) -> StateStore:
    """Factory: returns the appropriate StateStore variant."""
    if redis_url:
        from features.conversation.persistence.redis_store import RedisStateStore

        return RedisStateStore(redis_url, capture_spec=capture_spec)  # type: ignore[return-value]
    return StateStore(db_pool=db_pool, db_schema=db_schema, capture_spec=capture_spec)


# Default singleton (in-memory for dev/tests)
store = StateStore()
