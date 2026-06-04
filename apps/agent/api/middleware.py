"""HTTP middleware and rate-limiting helpers for the Sorelia FastAPI app.

Extracted from api/main.py to keep main.py thin. Contains:
- RateLimitMiddleware (basic sliding-window per IP)
- SecurityHeadersMiddleware (baseline security headers)
- IP-based daily limit helpers
- Hardened rate limiter (rate_limiter) and 429 helper
- CSRF + session token helpers
"""

from __future__ import annotations

import hashlib
import hmac
import time
from collections import defaultdict
from datetime import date

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from loguru import logger
from starlette.middleware.base import BaseHTTPMiddleware

from shared.config.settings import settings
from shared.rate_limit import from_settings as _build_rate_limiter

# --- Basic sliding-window rate limiter (legacy, per-IP) ---

_RATE_LIMIT = 10  # max requests per window
_RATE_WINDOW = 60  # seconds
_RATE_LIMITED_PATHS = {
    "/api/v1/chat",
    "/api/v1/comprobante",
}
_request_log: dict[str, list[float]] = defaultdict(list)


def _client_ip(request: Request) -> str:
    """Real client IP. Behind Traefik the connection IP is the proxy, so prefer
    the first hop of X-Forwarded-For; fall back to the connection IP."""
    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        first = xff.split(",")[0].strip()
        if first:
            return first
    return request.client.host if request.client else "unknown"


# --- Daily limit for visitors without visitor_id (IP fallback) ---
_daily_ip_counts: dict[str, tuple[str, int]] = {}  # ip -> (date_str, count)


def _check_ip_daily_limit(ip: str, limit: int) -> tuple[bool, int]:
    """In-memory daily limit check by IP.  Returns (allowed, remaining)."""
    today = date.today().isoformat()
    entry = _daily_ip_counts.get(ip)
    if entry is None or entry[0] != today:
        return True, limit
    count = entry[1]
    remaining = max(limit - count, 0)
    return remaining > 0, remaining


def _increment_ip_daily_count(ip: str) -> None:
    today = date.today().isoformat()
    entry = _daily_ip_counts.get(ip)
    if entry is None or entry[0] != today:
        _daily_ip_counts[ip] = (today, 1)
    else:
        _daily_ip_counts[ip] = (today, entry[1] + 1)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """In-memory per-IP coarse rate limiter for the message-bearing endpoints.

    This is the OUTERMOST, endpoint-agnostic guard (a flat cap per IP/window) so
    a flood on the message endpoints is shed before any app logic runs. The
    granular, attack-shaped limits (chat/min, DNI anti-enumeration, daily LLM
    spend, upload/hour) live in the route handlers via ``rate_limiter`` — they
    need request-body context (the DNI, the cost) the middleware doesn't have.

    Asset GETs (widget.js, /branding, favicon, …) are NOT in
    ``_RATE_LIMITED_PATHS`` → a normal page load (many asset requests) is never
    throttled here. Only chat / comprobante POSTs count.
    """

    async def dispatch(self, request: Request, call_next):
        if request.url.path not in _RATE_LIMITED_PATHS:
            return await call_next(request)

        client_ip = _client_ip(request)
        now = time.time()
        cutoff = now - _RATE_WINDOW

        # Prune old timestamps
        timestamps = _request_log[client_ip]
        _request_log[client_ip] = [t for t in timestamps if t > cutoff]

        if len(_request_log[client_ip]) >= _RATE_LIMIT:
            return _too_many_requests(_RATE_WINDOW)

        _request_log[client_ip].append(now)
        return await call_next(request)


# --- Hardened rate limiter (granular, attack-shaped) ---
# Singleton shared by the chat + comprobante handlers. In-memory (fine for the
# single-container staging deploy). The design is Redis-ready (COBRANZA_REDIS_URL)
# but in-memory is the active backend.
rate_limiter = _build_rate_limiter(settings)

# Neutral, peruvian-"tú" 429 messages. They must NOT leak which internal limit
# tripped (that would help an attacker tune). One short copy per surface.
_LIMIT_MSG_CHAT = (
    "Estás enviando mensajes muy seguido. Espera un momento y vuelve a "
    "intentarlo, o escríbenos por WhatsApp para una atención directa."
)
_LIMIT_MSG_COST = (
    "Por hoy ya cubrimos bastante por aquí. Vuelve más tarde o escríbenos por "
    "WhatsApp y un asesor te ayuda."
)
_LIMIT_MSG_UPLOAD = (
    "Recibimos varios comprobantes en poco tiempo. Espera un momento e "
    "inténtalo de nuevo."
)
_LIMIT_MSG_GENERIC = (
    "Demasiadas solicitudes en poco tiempo. Espera un momento e inténtalo de nuevo."
)


def _too_many_requests(retry_after: int, message: str = _LIMIT_MSG_GENERIC) -> Response:
    """Build a 429 with a ``Retry-After`` header and a neutral message."""
    return JSONResponse(
        status_code=429,
        content={"detail": message},
        headers={"Retry-After": str(max(int(retry_after), 1))},
    )


# --- Security headers middleware ---

# Content-Security-Policy for the landing + widget. Google Fonts + the tenant
# cloudfront logo are allowed. Inline <style>/<script> in index.html still need
# 'unsafe-inline' (the landing has substantial inline CSS/JS); everything else
# is locked to 'self'. connect-src 'self' covers the same-origin API.
_CSP = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline'; "
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
    "font-src 'self' https://fonts.gstatic.com; "
    "img-src 'self' data: https://d14bodb4yrsx8y.cloudfront.net; "
    "connect-src 'self'; "
    "frame-ancestors 'none'; "
    "base-uri 'self'; "
    "form-action 'self'"
)

_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
    "Content-Security-Policy": _CSP,
    "Permissions-Policy": "geolocation=(), microphone=(), camera=()",
}


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Attach baseline security headers to every response (MED-01)."""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        for key, value in _SECURITY_HEADERS.items():
            response.headers.setdefault(key, value)
        return response


# --- CSRF helpers ---

_CSRF_SECRET = settings.csrf_secret


def _generate_csrf_token() -> str:
    timestamp = str(int(time.time()))
    sig = hmac.new(_CSRF_SECRET.encode(), timestamp.encode(), hashlib.sha256).hexdigest()
    return f"{timestamp}_{sig}"


_CSRF_MAX_AGE = 3600  # tokens expire after 1 hour


def _validate_csrf_token(token: str) -> bool:
    if not token or "_" not in token:
        return False
    timestamp, sig = token.split("_", 1)
    # Verify signature
    expected = hmac.new(_CSRF_SECRET.encode(), timestamp.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        return False
    # Verify token age
    try:
        token_time = int(timestamp)
    except ValueError:
        return False
    if abs(time.time() - token_time) > _CSRF_MAX_AGE:
        return False
    return True


# --- Session token (proof-of-origin) ---

_SESSION_TOKEN_MAX_AGE = 3600  # 1 hour


def _generate_session_token(visitor_id: str) -> str:
    """Create an HMAC-signed session token binding visitor_id + timestamp."""
    timestamp = str(int(time.time()))
    payload = f"{visitor_id}:{timestamp}"
    signature = hmac.new(
        _CSRF_SECRET.encode(), payload.encode(), hashlib.sha256
    ).hexdigest()
    return f"{payload}:{signature}"


def _verify_session_token(token: str, max_age: int = _SESSION_TOKEN_MAX_AGE) -> tuple[bool, str]:
    """Verify HMAC session token. Returns (valid, visitor_id)."""
    if not token:
        return False, ""
    parts = token.split(":")
    if len(parts) != 3:
        return False, ""
    visitor_id, timestamp_str, signature = parts
    # Check expiry
    try:
        token_time = int(timestamp_str)
    except ValueError:
        return False, ""
    if time.time() - token_time > max_age:
        return False, ""
    # Check HMAC
    payload = f"{visitor_id}:{timestamp_str}"
    expected = hmac.new(
        _CSRF_SECRET.encode(), payload.encode(), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(signature, expected):
        return False, ""
    return True, visitor_id
