from app.services.login_lockout import check_lockout, record_failure, record_success
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Response, status, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.security import OAuth2PasswordRequestForm
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.config import settings
from app.core.database import get_db
from app.core.security import (
    create_access_token,
    decode_access_token,
    get_password_hash,
)
from app.dependencies import ACCESS_TOKEN_COOKIE, get_current_user, get_current_user_optional
from app.models.user import User
from app.schemas.user import (
    Token,
    UserCreate,
    UserResponse,
    ForgotPasswordRequest,
    ResetPasswordRequest,
)
from app.services.auth_service import authenticate_user, register_user
from app.services.email_service import send_password_reset_email

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["Authentication"])
templates = Jinja2Templates(directory="app/templates")

# Sliding-window rate limiter for auth endpoints
_auth_rate_limit_map: Dict[str, List[float]] = {}
AUTH_RATE_LIMIT_WINDOW_SECONDS = 60
MAX_AUTH_ATTEMPTS_PER_WINDOW = 5
MAX_AUTH_RATE_LIMIT_KEYS = 10_000  # Hard cap to prevent unbounded memory growth


def _check_auth_rate_limit(rate_key: str) -> bool:
    """Simple sliding window rate limiter for login/register."""
    now = time.time()

    # Evict the oldest key when at the hard cap (attacker-controlled IPs)
    if len(_auth_rate_limit_map) >= MAX_AUTH_RATE_LIMIT_KEYS and rate_key not in _auth_rate_limit_map:
        try:
            oldest_key = next(iter(_auth_rate_limit_map))
            del _auth_rate_limit_map[oldest_key]
        except StopIteration:
            pass

    timestamps = _auth_rate_limit_map.get(rate_key, [])
    timestamps = [ts for ts in timestamps if now - ts <= AUTH_RATE_LIMIT_WINDOW_SECONDS]
    if len(timestamps) >= MAX_AUTH_ATTEMPTS_PER_WINDOW:
        _auth_rate_limit_map[rate_key] = timestamps
        return False
    timestamps.append(now)
    _auth_rate_limit_map[rate_key] = timestamps
    return True


def _client_ip(request: Request) -> str:
    """Client IP for rate limiting/lockout keys; honors X-Forwarded-For behind a trusted proxy."""
    if getattr(settings, "TRUST_PROXY", False):
        xff = request.headers.get("x-forwarded-for", "")
        if xff:
            return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _set_access_cookie(response: Response, access_token: str) -> None:
    """Attach HttpOnly session cookie for SSR pages."""
    max_age = int(getattr(settings, "ACCESS_TOKEN_EXPIRE_MINUTES", 1440)) * 60
    is_production = not getattr(settings, "DEBUG", True)

    response.set_cookie(
        key=ACCESS_TOKEN_COOKIE,
        value=access_token,
        httponly=True,
        secure=is_production,
        samesite="lax",
        max_age=max_age,
        path="/",
    )


def _clear_access_cookie(response: Response) -> None:
    response.delete_cookie(
        key=ACCESS_TOKEN_COOKIE,
        path="/",
        samesite="lax",
    )


def _create_reset_token(user_id: int) -> str:
    """Create a 15-minute password-reset token.

    Reuses the project's python-jose signer with a namespaced subject
    ("reset:<id>") so it can NEVER double as a login session token —
    dependencies._user_from_token expects a plain integer subject and will
    reject non-numeric subs. `ver=0` is acceptable here because reset tokens
    are never resolved to a session user.
    """
    return create_access_token(
        subject=f"reset:{user_id}",
        token_version=0,
        expires_delta=timedelta(minutes=15),
    )


def _verify_reset_token(token: str, user: Optional[User] = None) -> int:
    """Verify a reset token and return the user ID, or raise 400.

    When a user is provided, rejects tokens issued before the user's last
    password change (single-use reset links).
    """
    payload = decode_access_token(token)
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired reset link.",
        )

    sub = payload.get("sub", "")
    if not isinstance(sub, str) or not sub.startswith("reset:"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid reset token.",
        )

    try:
        user_id = int(sub.split(":", 1)[1])
    except (ValueError, IndexError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Malformed reset token.",
        )

    if user is not None and user.password_changed_at is not None:
        changed_at = user.password_changed_at
        if changed_at.tzinfo is None:
            changed_at = changed_at.replace(tzinfo=timezone.utc)
        iat = payload.get("iat")
        if not isinstance(iat, (int, float)) or int(iat) <= int(changed_at.timestamp()):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="This reset link has already been used.",
            )
    return user_id


@router.post(
    "/register",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
)
def register(
    request: Request,
    user_in: UserCreate,
    db: Session = Depends(get_db),
):
    """Register a new learner account (JSON body)."""

    RESERVED_EMAILS = {"guest@example.com", "admin@smartreco.ai", "demo@smartreco.ai"}
    if user_in.email.lower() in RESERVED_EMAILS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This email address is reserved for system use.",
        )

    client_ip = _client_ip(request)
    rate_key = f"auth_register:{client_ip}"
    if not _check_auth_rate_limit(rate_key):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many registration attempts. Max 5 per minute.",
        )

    try:
        user = register_user(db, user_in)
        return user
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )


@router.post("/login", response_model=Token)
def login(
    request: Request,
    response: Response,
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db),
):
    """Authenticate with email + password. Sets HttpOnly cookie + returns JSON token."""
    client_ip = _client_ip(request)
    rate_key = f"auth_login:{client_ip}"
    if not _check_auth_rate_limit(rate_key):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many login attempts. Max 5 per minute.",
        )

    # Rev5 G4.2: Check dual-key lockout BEFORE attempting auth
    email_input = form_data.username.strip().lower()
    locked, retry_after = check_lockout(client_ip, email_input)
    if locked:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Account or IP is temporarily locked due to too many failed attempts. Try again in {retry_after // 60} minutes.",
            headers={"Retry-After": str(retry_after)},
        )

    try:
        user = authenticate_user(db, form_data.username, form_data.password)
    except ValueError as e:
        # e.g. deactivated account — distinguishable from bad credentials
        record_failure(client_ip, email_input)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(e),
        )
    if not user:
        record_failure(client_ip, email_input)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Successful auth — clear lockout counters
    record_success(client_ip, user.email)

    # Rev5 G4.3: embed current token_version as `ver` claim so the token can
    # be revoked server-side by bumping user.token_version.
    access_token = create_access_token(
        subject=user.id,
        token_version=user.token_version,
    )
    _set_access_cookie(response, access_token)

    return {
        "access_token": access_token,
        "token_type": "bearer",
    }

@router.post("/logout")
def logout(
    response: Response,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Clear session cookie AND revoke every other session the user has open.

    Rev5 G4.3: bumping token_version invalidates all JWTs issued before this
    call — every open tab/device is signed out at once without needing a
    token store.
    """
    # Bump token_version to revoke all existing sessions
    current_user.token_version = (current_user.token_version or 0) + 1
    db.commit()
    logger.info(f"[logout] Revoked all sessions for user #{current_user.id}")

    _clear_access_cookie(response)
    return {"status": "ok", "detail": "Logged out"}


@router.get("/me", response_model=UserResponse)
def get_me(current_user: User = Depends(get_current_user)):
    """Return the currently authenticated user (cookie or Bearer)."""
    return current_user


# ============================================================
# Forgot / Reset Password
# ============================================================


@router.post("/forgot-password")
def forgot_password(
    request: Request,
    response: Response,
    payload: ForgotPasswordRequest,
    db: Session = Depends(get_db),
):
    """
    Request a password reset link. Always returns 200 (even if the email isn't
    in the system) to avoid email enumeration attacks.
    """
    client_ip = _client_ip(request)
    rate_key = f"auth_forgot:{client_ip}"
    if not _check_auth_rate_limit(rate_key):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests. Try again in a minute.",
        )

    email = payload.email.strip().lower()
    if not email:
        return {"status": "sent"}  # never reveal whether the email exists

    user = db.query(User).filter(User.email.ilike(email)).first()
    if not user:
        logger.info(f"[forgot-password] Unknown email requested reset: {email}")
        return {"status": "sent"}

    # Skip placeholder emails (seeded / system accounts)
    domain = email.split("@", 1)[1] if "@" in email else ""
    placeholder_domains = {
        "example.com", "smartreco.ai", "aura.com", "test.com", "localhost",
        # per-session anonymous visitor accounts created by dependencies
        "smartreco.local",
    }
    if domain in placeholder_domains:
        logger.info(f"[forgot-password] Skipping placeholder domain: {domain}")
        return {"status": "sent"}

    token = _create_reset_token(user.id)
    try:
        send_password_reset_email(user_email=email, reset_token=token)
        logger.info(f"[forgot-password] Reset email sent to {email}")
    except Exception as e:
        logger.error(f"[forgot-password] Email send failed for {email}: {e}")
        # Still return success to avoid enumeration

    return {"status": "sent"}


@router.post("/reset-password")
def reset_password(
    payload: ResetPasswordRequest,
    db: Session = Depends(get_db),
):
    """Validate the reset token and update the user's password."""
    token = payload.token.strip()
    new_password = payload.new_password

    user_id = _verify_reset_token(token)
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found.",
        )

    # Single-use: reject tokens issued before the last password change.
    _verify_reset_token(token, user)

    user.hashed_password = get_password_hash(new_password)
    # Rev5 G4.3: bump token_version so all pre-reset sessions are revoked.
    # The user must log in again with their new password.
    user.token_version = (user.token_version or 0) + 1
    user.password_changed_at = datetime.now(timezone.utc)
    db.commit()

    logger.info(f"[reset-password] Password updated + all sessions revoked for user #{user.id} ({user.email})")
    return {"status": "ok", "detail": "Password updated successfully."}


@router.get("/reset-page", response_class=HTMLResponse)
def page_reset_password(
    request: Request,
    token: str = "",
    user: User = Depends(get_current_user_optional),
):
    """Render the set-new-password page, pre-validating the token."""
    error = None
    if token:
        try:
            _verify_reset_token(token)
        except HTTPException as exc:
            error = exc.detail

    return templates.TemplateResponse(
        request,
        "pages/reset_password.html",
        {"user": user, "token": token, "error": error},
    )


@router.get("/forgot-page", response_class=HTMLResponse)
def page_forgot_password(
    request: Request,
    user: User = Depends(get_current_user_optional),
):
    """Render the forgot-password form."""
    if user:
        return RedirectResponse(url="/dashboard", status_code=302)
    return templates.TemplateResponse(
        request,
        "pages/forgot_password.html",
        {"user": user},
    )