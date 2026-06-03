"""ChatHub web publisher tests (camino C, Movistar pattern).

All network is mocked — we monkeypatch ``httpx.AsyncClient`` so no real POST goes
out. Coverage:
  · payload is the exact IncomingOlimpoMessage shape (channel/contact/message/receiver)
  · NO-OP when channel_id is empty (publisher disabled)
  · errors are swallowed (never propagate) and return False
  · timestamp uses the Olimpo/Doris format ``YYYY-MM-DD HH:MM:SS``
  · message.id is a uuid4
"""

from __future__ import annotations

import re
import uuid

import pytest

from features.messaging import chathub_web_publisher as pub
from features.messaging.chathub_web_publisher import build_incoming_payload, publish_to_chathub

_TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")


# ── Fake httpx layer ─────────────────────────────────────────────────────────


class _FakeResponse:
    def __init__(self, status_code: int = 200):
        self.status_code = status_code


class _FakeAsyncClient:
    """Captures the POST args and returns a canned response. Records every
    instance's init kwargs and the last post() call on the class for assertions."""

    last_post: dict | None = None
    last_init: dict | None = None
    status_code = 200
    raise_on_post = False

    def __init__(self, **kwargs):
        type(self).last_init = kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, json=None):
        type(self).last_post = {"url": url, "json": json}
        if type(self).raise_on_post:
            raise RuntimeError("boom — network down")
        return _FakeResponse(type(self).status_code)


@pytest.fixture
def fake_httpx(monkeypatch):
    _FakeAsyncClient.last_post = None
    _FakeAsyncClient.last_init = None
    _FakeAsyncClient.status_code = 200
    _FakeAsyncClient.raise_on_post = False
    monkeypatch.setattr(pub.httpx, "AsyncClient", _FakeAsyncClient)
    return _FakeAsyncClient


_RECEIVER = {"type": "group", "identifier": "7"}


# ── build_incoming_payload (pure) ────────────────────────────────────────────


def test_payload_shape_exact():
    body = build_incoming_payload(
        channel_id="ch-123",
        session_id="sess-abc",
        contact_name="Juan Pérez",
        text="Quiero hablar con un asesor",
        receiver=_RECEIVER,
    )
    assert body["metadata"] == {}
    assert body["channel"] == {"id": "ch-123"}
    assert body["receiver"] == _RECEIVER
    assert body["contact"] == {"id": "sess-abc", "name": "Juan Pérez"}
    msg = body["message"]
    assert msg["from"] == "sess-abc"
    assert msg["type"] == "text"
    assert msg["text"] == {"body": "Quiero hablar con un asesor"}


def test_payload_contact_name_falls_back_to_session_id():
    body = build_incoming_payload(
        channel_id="ch-1", session_id="sess-1", contact_name="", text="hola", receiver=_RECEIVER
    )
    assert body["contact"]["name"] == "sess-1"


def test_payload_message_id_is_uuid4():
    body = build_incoming_payload(
        channel_id="ch-1", session_id="s", contact_name="n", text="t", receiver=_RECEIVER
    )
    # Parses as a uuid4 (raises if malformed).
    parsed = uuid.UUID(body["message"]["id"])
    assert parsed.version == 4


def test_payload_timestamp_olimpo_format():
    body = build_incoming_payload(
        channel_id="ch-1", session_id="s", contact_name="n", text="t", receiver=_RECEIVER
    )
    assert _TS_RE.match(body["message"]["timestamp"])


# ── publish_to_chathub ────────────────────────────────────────────────────────


async def test_publish_sends_correct_payload(fake_httpx):
    ok = await publish_to_chathub(
        "ch-xyz", "sess-9", "Maria", "necesito ayuda", _RECEIVER, timeout=5
    )
    assert ok is True
    sent = fake_httpx.last_post
    assert sent is not None
    assert sent["url"].endswith("/olimpo/incomingMessage")
    payload = sent["json"]
    assert payload["channel"] == {"id": "ch-xyz"}
    assert payload["contact"] == {"id": "sess-9", "name": "Maria"}
    assert payload["receiver"] == _RECEIVER
    assert payload["message"]["text"]["body"] == "necesito ayuda"
    assert payload["message"]["from"] == "sess-9"


async def test_publish_noop_without_channel_id(fake_httpx):
    ok = await publish_to_chathub("", "sess-1", "Juan", "hola", _RECEIVER)
    assert ok is False
    # No POST attempted at all.
    assert fake_httpx.last_post is None


async def test_publish_swallows_network_error(fake_httpx):
    fake_httpx.raise_on_post = True
    # Must not raise — returns False on swallowed error.
    ok = await publish_to_chathub("ch-1", "sess-1", "Juan", "hola", _RECEIVER)
    assert ok is False


async def test_publish_swallows_non_2xx(fake_httpx):
    fake_httpx.status_code = 500
    ok = await publish_to_chathub("ch-1", "sess-1", "Juan", "hola", _RECEIVER)
    assert ok is False


async def test_publish_respects_verify_ssl_setting(fake_httpx):
    await publish_to_chathub("ch-1", "sess-1", "Juan", "hola", _RECEIVER)
    # Default config: verify_ssl is False (self-signed host).
    assert fake_httpx.last_init.get("verify") is False
