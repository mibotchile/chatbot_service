"""Security endpoints: CSRF token + session token."""

from __future__ import annotations

from fastapi import APIRouter, Request, Response

from api.middleware import (
    _generate_csrf_token,
    _generate_session_token,
    _SESSION_TOKEN_MAX_AGE,
)

router = APIRouter()


@router.get("/api/v1/security/csrf-token")
async def csrf_token(response: Response):
    token = _generate_csrf_token()
    response.headers["X-CSRF-Token"] = token
    response.set_cookie("csrf_token", token, httponly=True, samesite="lax", secure=True)
    return {"status": "ok"}


@router.get("/api/v1/security/session-token")
async def session_token(request: Request):
    """Issue a time-limited HMAC session token tied to visitor_id."""
    visitor_id = request.query_params.get("visitor_id", "anonymous")
    token = _generate_session_token(visitor_id)
    return {"token": token, "expires_in": _SESSION_TOKEN_MAX_AGE}
