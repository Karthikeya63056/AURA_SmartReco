import logging
import secrets
import time
from typing import Dict, List

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
    verify_password,
)
from app.dependencies import ACCESS_TOKEN_COOKIE, get_current_user, get_current_user_optional
from app.models.user import User
from app.schemas.user import Token, UserCreate, UserResponse
from app.services.auth_service import authenticate_user, register_user
from app.services.email_service import send_password_reset_email

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["Authentication"])
templates = Jinja2Templates(directory="app/templates")

# Sliding-window rate limiter for auth endpoints
_auth_rate_limit_map: Dict[str, List[float]] = {}
AUTH_RATE_LIMIT_WINDOW_SECONDS = 60
MAX_AUTH_ATTEMPTS_PER_WINDOW = 5


def _check_auth_rate_limit(rate_key: str) -> bool:
    """Simple sliding window rate limiter for login/register."""
    now = time.time()
    timestamps = _auth_rate_limit_map.get(rate_key, [])
    timestamps = [ts for ts in timestamps if now - ts <= AUTH_RATE_LIMIT_WINDOW_SECONDS]
    if len(timestamps) >= MAX_AUTH_ATTEMPTS_PER_WINDOW:
        _auth_rate_limit_map[rate_key] = timestamps
        return False
    timestamps.append(now)
    _auth_rate_limit_map[rate_key] = timestamps
    return True


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
    """Create a short-lived JWT reset token (15 min TTL)."""
    from datetime import datetime, timedelta, timezone

    expires = datetime.now(timezone.utc) + timedelta(minutes=15)
    payload = {
        "sub": f"reset:{user_id}",
        "exp": expires,
        "jti": secrets.token_urlsafe(16),  # unique per issuance
    }
    # Reuse the same signing utility with a different subject prefix
    import jwt

    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def _verify_reset_token(token: str) -> int:
    """Verify a reset token and return the user ID, or raise."""
    import jwt

    try:
        payload = jwt.decode(
            token,
            settings.JWT_SECRET,
            algorithms=[settings.JWT_ALGORITHM],
        )
    except jwt.PyJWTError:
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
        return int(sub.split(":", 1)[1])
    except (ValueError, IndexError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Malformed reset token.",
        )


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

    client_ip = request.client.host if request.client else "unknown"
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
    client_ip = request.client.host if request.client else "unknown"
    rate_key = f"auth_login:{client_ip}"
    if not _check_auth_rate_limit(rate_key):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many login attempts. Max 5 per minute.",
        )

    user = authenticate_user(db, form_data.username, form_data.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    access_token = create_access_token(subject=user.id)
    _set_access_cookie(response, access_token)

    return {
        "access_token": access_token,
        "token_type": "bearer",
    }


@router.post("/logout")
def logout(response: Response):
    """Clear session cookie. Safe to call even if already logged out."""
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
    payload: Dict,
    db: Session = Depends(get_db),
):
    """
    Request a password reset link. Always returns 200 (even if the email isn't
    in the system) to avoid email enumeration attacks.
    """
    client_ip = request.client.host if request.client else "unknown"
    rate_key = f"auth_forgot:{client_ip}"
    if not _check_auth_rate_limit(rate_key):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests. Try again in a minute.",
        )

    email = (payload.get("email") or "").strip().lower()
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
    payload: Dict,
    db: Session = Depends(get_db),
):
    """Validate the reset token and update the user's password."""
    token = (payload.get("token") or "").strip()
    new_password = payload.get("new_password") or ""

    if not token or not new_password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Token and new password are required.",
        )

    if len(new_password) < 8:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Password must be at least 8 characters.",
        )

    user_id = _verify_reset_token(token)
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found.",
        )

    user.hashed_password = get_password_hash(new_password)
    db.commit()

    logger.info(f"[reset-password] Password updated for user #{user.id} ({user.email})")
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
        "pages/reset_password.html",
        {"request": request, "user": user, "token": token, "error": error},
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
        "pages/forgot_password.html",
        {"request": request, "user": user},
    )