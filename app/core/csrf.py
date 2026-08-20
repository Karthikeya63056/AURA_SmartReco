"""
CSRF double-submit middleware (Rev5 §5.12, gate G4.4).

On every response, sets a `csrf_token` cookie (readable by JS, NOT HttpOnly).
On state-changing requests (POST/PUT/PATCH/DELETE) with cookie auth, requires
the `X-CSRF-Token` header to match the cookie value using constant-time
comparison. Bearer-token auth is exempt (API clients / Swagger don't
auto-send cookies).

Scale-up: same as auth — single-process in-memory. No multi-worker claim.
"""
import hmac
import logging
import secrets
from typing import Tuple

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response, JSONResponse

logger = logging.getLogger(__name__)

CSRF_COOKIE_NAME = "csrf_token"
CSRF_HEADER_NAME = "x-csrf-token"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
TOKEN_LENGTH_BYTES = 32


def generate_csrf_token() -> str:
    """Generate a cryptographically random CSRF token."""
    return secrets.token_urlsafe(TOKEN_LENGTH_BYTES)


def _verify_token(cookie_token: str, header_token: str) -> bool:
    """Constant-time comparison to avoid timing attacks."""
    if not cookie_token or not header_token:
        return False
    return hmac.compare_digest(cookie_token.encode("utf-8"), header_token.encode("utf-8"))


def _has_cookie_auth(request: Request) -> bool:
    """Return True if the request carries the access_token cookie."""
    return bool(request.cookies.get("access_token"))


def _has_bearer_auth(request: Request) -> bool:
    """Return True if the request carries an Authorization: Bearer header."""
    auth_header = request.headers.get("authorization", "")
    return auth_header.lower().startswith("bearer ")


class CSRFMiddleware(BaseHTTPMiddleware):
    """
    CSRF protection middleware.

    - Sets `csrf_token` cookie on every response (if not already set).
    - On unsafe methods + cookie auth: requires matching X-CSRF-Token header.
    - Exempts Bearer-token auth (API clients).
    - Exempts safe methods (GET/HEAD/OPTIONS).
    """

    async def dispatch(self, request: Request, call_next):
        # Always ensure a CSRF token cookie exists
        cookie_token = request.cookies.get(CSRF_COOKIE_NAME)
        if not cookie_token:
            cookie_token = generate_csrf_token()

        # Check CSRF on unsafe methods with cookie auth
        if request.method not in SAFE_METHODS and _has_cookie_auth(request) and not _has_bearer_auth(request):
            header_token = request.headers.get(CSRF_HEADER_NAME, "")
            if not _verify_token(cookie_token, header_token):
                logger.warning(
                    f"[CSRF] Rejected {request.method} {request.url.path} "
                    f"from {request.client.host if request.client else 'unknown'}"
                )
                return JSONResponse(
                    status_code=403,
                    content={"detail": "CSRF validation failed. Include X-CSRF-Token header matching the csrf_token cookie."},
                )

        # Continue to the endpoint
        response = await call_next(request)

        # Set the CSRF cookie on the response (if not already set)
        if CSRF_COOKIE_NAME not in request.cookies:
            response.set_cookie(
                key=CSRF_COOKIE_NAME,
                value=cookie_token,
                httponly=False,  # JS must read this to send it back
                secure=not getattr(request.app.state, "debug", True),
                samesite="lax",
                max_age=86400 * 30,  # 30 days
                path="/",
            )

        return response