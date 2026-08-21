import hashlib
import logging
import secrets
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import decode_access_token, get_password_hash, verify_token_version
from app.models.agent_run import AgentRun
from app.models.anonymous_session import AnonymousSession
from app.models.event import Event
from app.models.recommendation import Recommendation
from app.models.user import User
from app.models.user_profile import UserProfile
from app.models.wishlist import WishlistItem

logger = logging.getLogger(__name__)

# Used by Swagger / API clients (Authorization: Bearer …)
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login", auto_error=False)

# Cookie name set by /auth/login
ACCESS_TOKEN_COOKIE = "access_token"

GUEST_EMAIL = "guest@example.com"

# Per-session anonymous visitor accounts
ANON_EMAIL_DOMAIN = "guest.smartreco.local"
ANON_SESSION_TTL_DAYS = 30
_ANON_GC_INTERVAL_SECONDS = 3600
_last_anon_gc_time = 0.0


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
    """
    Decode the JWT, load the user, and enforce both deactivation (is_active)
    and server-side revocation (token_version == JWT `ver` claim).

    Rev5 G4.3: bumping users.token_version instantly invalidates every JWT
    issued before the bump — no token store needed, no extra queries beyond
    the single user lookup we already do.
    """
    payload = decode_access_token(token)
    if not payload or "sub" not in payload:
        return None
    try:
        user_id = int(payload["sub"])
    except (TypeError, ValueError):
        # Non-numeric subjects (e.g. "reset:123" from password-reset tokens)
        # must never resolve to a session user.
        return None
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        return None
    # Deactivated accounts must not keep using their cookie/bearer session
    if not user.is_active:
        return None
    # Rev5 G4.3: `ver` claim must match current token_version
    if not verify_token_version(payload, user.token_version):
        return None
    return user


def _anon_session_key(session_id: str) -> str:
    """Deterministic, opaque key for a client-supplied session id."""
    return hashlib.sha256(session_id.encode("utf-8")).hexdigest()


def _gc_stale_anonymous_sessions(db: Session) -> None:
    """
    Delete anonymous session mappings (and their user rows) that have been
    inactive for > ANON_SESSION_TTL_DAYS. Runs at most once per hour.
    Users that still have events, recommendations, wishlist items, or agent
    runs are kept (mapping only removed) so history is never silently dropped.
    Deletes run in FK-safe order: AnonymousSession → UserProfile → User.
    """
    global _last_anon_gc_time
    now = time.time()
    if now - _last_anon_gc_time < _ANON_GC_INTERVAL_SECONDS:
        return
    _last_anon_gc_time = now

    cutoff = datetime.now(timezone.utc) - timedelta(days=ANON_SESSION_TTL_DAYS)
    stale = (
        db.query(AnonymousSession)
        .filter(AnonymousSession.last_seen_at < cutoff)
        .all()
    )
    if not stale:
        return

    stale_user_ids = [row.user_id for row in stale]
    users_with_data = set()
    users_with_data.update(
        row[0] for row in db.query(Event.user_id).filter(Event.user_id.in_(stale_user_ids))
    )
    users_with_data.update(
        row[0] for row in db.query(Recommendation.user_id).filter(Recommendation.user_id.in_(stale_user_ids))
    )
    users_with_data.update(
        row[0] for row in db.query(WishlistItem.user_id).filter(WishlistItem.user_id.in_(stale_user_ids))
    )
    users_with_data.update(
        row[0] for row in db.query(AgentRun.user_id).filter(AgentRun.user_id.in_(stale_user_ids))
    )
    removable = [uid for uid in stale_user_ids if uid not in users_with_data]

    # FK-safe order: mapping rows first, then profile, then the user row
    db.query(AnonymousSession).filter(AnonymousSession.id.in_([row.id for row in stale])).delete(
        synchronize_session=False
    )
    if removable:
        db.query(UserProfile).filter(UserProfile.user_id.in_(removable)).delete(synchronize_session=False)
        db.query(User).filter(User.id.in_(removable)).delete(synchronize_session=False)

    db.commit()


def _get_shared_guest(db: Session) -> User:
    """Legacy fallback guest when no session_id is available (API-only clients)."""
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


def _create_anonymous_session_user(db: Session, session_key: str) -> User:
    """Create (or fetch, on race) the isolated user for an anonymous session."""
    email = f"anon_{session_key[:16]}@{ANON_EMAIL_DOMAIN}"
    user = db.query(User).filter(User.email == email).first()
    if user:
        return user

    user = User(
        email=email,
        hashed_password=get_password_hash(secrets.token_urlsafe(32)),
        full_name="Anonymous Visitor",
        is_admin=False,
    )
    db.add(user)
    try:
        db.commit()
        db.refresh(user)
    except Exception:
        db.rollback()  # concurrent request created the same user — reuse it
        user = db.query(User).filter(User.email == email).first()
        if not user:
            raise

    anon_session = AnonymousSession(id=session_key, user_id=user.id)
    db.add(anon_session)
    try:
        db.commit()
    except Exception:
        db.rollback()  # concurrent request created the mapping — reuse it
    return user


def get_anonymous_user(db: Session, session_id: Optional[str] = None) -> User:
    """
    Anonymous user for unauthenticated event tracking.

    - With a session_id: returns a dedicated per-browser-session user row so
      anonymous visitors never share one global profile. Mapping is stable via
      an opaque hash of the session_id (never a raw client string).
    - Without one (legacy clients): shared guest account fallback.
    """
    _gc_stale_anonymous_sessions(db)

    if session_id:
        session_id = session_id.strip()[:128]
        if session_id:
            session_key = _anon_session_key(session_id)
            anon_session = (
                db.query(AnonymousSession)
                .filter(AnonymousSession.id == session_key)
                .first()
            )
            if anon_session:
                anon_session.last_seen_at = datetime.now(timezone.utc)
                try:
                    db.commit()
                except Exception:
                    db.rollback()
                user = db.query(User).filter(User.id == anon_session.user_id).first()
                if user:
                    return user
            return _create_anonymous_session_user(db, session_key)

    return _get_shared_guest(db)


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