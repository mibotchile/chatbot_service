"""Unit tests for the CORS '*' wildcard rejection guard (Slice B, task B-1).

TDD: written RED-first before the guard is added to cors.py.
"""

from __future__ import annotations

import logging
import re

import pytest


def test_collect_embed_origins_rejects_wildcard(monkeypatch, tmp_path, caplog):
    """A '*' entry in embed_origins must be dropped and a warning logged."""
    import json
    import shared.config.cors as cors

    t = tmp_path / "badtenant"
    t.mkdir()
    (t / "tenant.config.json").write_text(
        json.dumps({
            "id": "badtenant",
            "embed_origins": ["*", "https://legitimate.example.com"],
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(cors, "_tenants_root", lambda: tmp_path)

    with caplog.at_level(logging.WARNING, logger="shared.config.cors"):
        origins = cors.collect_embed_origins()

    # '*' must NOT appear in the result
    assert "*" not in origins
    # The legitimate origin must still be present
    assert "https://legitimate.example.com" in origins
    # A warning must have been emitted
    assert any("*" in rec.message or "wildcard" in rec.message.lower() for rec in caplog.records)


def test_wildcard_not_compiled_into_cors_regex(monkeypatch, tmp_path):
    """'*' must not produce a fragment that matches arbitrary origins."""
    import json
    import shared.config.cors as cors

    t = tmp_path / "badtenant"
    t.mkdir()
    (t / "tenant.config.json").write_text(
        json.dumps({
            "id": "badtenant",
            "embed_origins": ["*"],
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(cors, "_tenants_root", lambda: tmp_path)

    regex = cors.build_cors_origin_regex([])
    # With only '*' (which should be dropped), the regex must match NOTHING
    rx = re.compile(regex)
    assert not rx.match("https://anything.example.com")
    assert not rx.match("*")
    assert not rx.match("")


def test_legitimate_origins_unaffected_by_wildcard_guard(monkeypatch, tmp_path):
    """Legitimate origins must still be collected even when '*' co-exists."""
    import json
    import shared.config.cors as cors

    t = tmp_path / "tenant1"
    t.mkdir()
    (t / "tenant.config.json").write_text(
        json.dumps({
            "id": "tenant1",
            "embed_origins": ["*", "https://good.example.com", "http://localhost:*"],
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(cors, "_tenants_root", lambda: tmp_path)

    origins = cors.collect_embed_origins()
    assert "https://good.example.com" in origins
    assert "http://localhost:*" in origins
    assert "*" not in origins

    rx = re.compile(cors.build_cors_origin_regex([]))
    assert rx.match("https://good.example.com")
    assert rx.match("http://localhost:3000")
    assert not rx.match("https://evil.example.com")
