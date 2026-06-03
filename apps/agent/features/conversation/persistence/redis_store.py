"""Redis-backed conversation state store with 24h TTL."""

import json
import uuid

from redis.asyncio import Redis

from features.conversation.hooks import extract_implicit_data
from features.conversation.debtor_state import DebtorState

TTL_SECONDS = 86400  # 24 hours


def _key(conversation_id: str, suffix: str) -> str:
    return f"sorelia:conv:{conversation_id}:{suffix}"


class RedisConversationState:
    """Conversation state backed by Redis. Mirrors ConversationState interface."""

    def __init__(self, conversation_id: str, redis: Redis):
        self.conversation_id = conversation_id
        self._redis = redis
        self.debtor = DebtorState()
        self.page_context: dict = {}
        self.history: list[dict] = []
        self._dirty_history = False
        self._dirty_lead = False
        self._dirty_page = False

    def add_user_message(self, text: str) -> None:
        self.history.append({"role": "user", "content": text})
        self._dirty_history = True
        extracted = extract_implicit_data(text)
        if extracted:
            self.debtor.update(extracted)
            self._dirty_lead = True

    def add_assistant_message(self, content: str) -> None:
        self.history.append({"role": "assistant", "content": content})
        self._dirty_history = True

    async def load(self) -> None:
        """Load state from Redis into local attributes."""
        pipe = self._redis.pipeline()
        pipe.get(_key(self.conversation_id, "history"))
        pipe.get(_key(self.conversation_id, "debtor_data"))
        pipe.get(_key(self.conversation_id, "lead_data"))  # fallback (24h TTL overlap)
        pipe.get(_key(self.conversation_id, "page_context"))
        history_raw, debtor_raw, lead_raw, page_raw = await pipe.execute()

        if history_raw:
            self.history = json.loads(history_raw)
        # Dual-read: prefer debtor_data key; fall back to lead_data key
        raw = debtor_raw or lead_raw
        if raw:
            self.debtor = DebtorState(initial_data=json.loads(raw))
        if page_raw:
            self.page_context = json.loads(page_raw)

    async def save(self) -> None:
        """Persist dirty state to Redis with TTL refresh."""
        pipe = self._redis.pipeline()
        if self._dirty_history:
            key = _key(self.conversation_id, "history")
            pipe.set(key, json.dumps(self.history), ex=TTL_SECONDS)
            self._dirty_history = False
        if self._dirty_lead:
            key = _key(self.conversation_id, "debtor_data")
            pipe.set(key, json.dumps(self.debtor.collected), ex=TTL_SECONDS)
            self._dirty_lead = False
        if self._dirty_page:
            key = _key(self.conversation_id, "page_context")
            pipe.set(key, json.dumps(self.page_context), ex=TTL_SECONDS)
            self._dirty_page = False
        await pipe.execute()

    def set_page_context(self, ctx: dict) -> None:
        self.page_context = ctx
        self._dirty_page = True


class RedisStateStore:
    """Redis-backed store for conversation states."""

    def __init__(self, redis_url: str):
        self._redis = Redis.from_url(redis_url, decode_responses=True)

    async def get_or_create(self, conversation_id: str | None) -> RedisConversationState:
        if not conversation_id:
            conversation_id = str(uuid.uuid4())
        state = RedisConversationState(conversation_id, self._redis)
        await state.load()
        return state

    async def close(self) -> None:
        await self._redis.aclose()
