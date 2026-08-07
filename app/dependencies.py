import logging
from typing import Optional

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import decode_access_token, get_password_hash
from app.models.user import User

logger = logging.getLogger(__name__)

# Used by Swagger / API clients (Authorization: Bearer …)
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login", auto_error=False)

# Cookie name set by /auth/login
ACCESS_TOKEN_COOKIE = "access_token"

GUEST_EMAIL = "guest@example.com"


def _extract_token(
    request: Request,
    header_token: Optional[str],
) -> Optional[str]:
    """
    Prefer HttpOnly cookie (SSR pages), then Authorization Bearer (API / Swagger).
    Cookie value may be raw JWT or "Bearer <jwt>".
    """
    cookie_val = request.cookies.get(ACCESS_TOKEN_COOKIE)
    if cookie_val:
        raw = cookie_val.strip()
        if raw.lower().startswith("bearer "):
            return raw.split(" ", 1)[1].strip()
        return raw

    if header_token:
        return header_token.strip()

    return None


def _user_from_token(db: Session, token: str) -> Optional[User]:
    payload = decode_access_token(token)
    if not payload or "sub" not in payload:
        return None
    try:
        user_id = int(payload["sub"])
    except (TypeError, ValueError):
        return None
    return db.query(User).filter(User.id == user_id).first()


def get_anonymous_user(db: Session) -> User:
    """
    Guest user for unauthenticated event tracking.
    Look up by email (no hard-coded primary key).
    """
    user = db.query(User).filter(User.email == GUEST_EMAIL).first()
    if user:
        return user

    user = User(
        email=GUEST_EMAIL,
        hashed_password=get_password_hash("guest-demo-password-not-for-login"),
        full_name="Guest Demo User",
        is_admin=False,
    )
    db.add(user)
    try:
        db.commit()
        db.refresh(user)
    except Exception:
        db.rollback()
        user = db.query(User).filter(User.email == GUEST_EMAIL).first()
        if not user:
            raise
    return user


def get_current_user_optional(
    request: Request,
    header_token: Optional[str] = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> Optional[User]:
    """
    Return User if a valid JWT is present (cookie or Bearer header).
    Used by public HTML pages and optional API auth.
    """
    token = _extract_token(request, header_token)
    if not token:
        return None
    try:
        return _user_from_token(db, token)
    except Exception:
        logger.debug("Optional auth failed to resolve user", exc_info=True)
        return None


def get_current_user(
    current_user: Optional[User] = Depends(get_current_user_optional),
) -> User:
    """Require an authenticated user."""
    if not current_user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return current_user


def get_admin_user(
    current_user: User = Depends(get_current_user),
) -> User:
    """Require admin privileges."""
    if not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin privileges required",
        )
    return current_user