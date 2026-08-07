import logging
import time
from typing import Dict, List

from fastapi import APIRouter, Depends, HTTPException, Response, status, Request
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from app.config import settings
from app.core.database import get_db
from app.core.security import create_access_token
from app.dependencies import ACCESS_TOKEN_COOKIE, get_current_user
from app.models.user import User
from app.schemas.user import Token, UserCreate, UserResponse
from app.services.auth_service import authenticate_user, register_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["Authentication"])

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

    # Dynamic Secure flag:
    # - False on local HTTP (DEBUG=True or weak/default JWT_SECRET)
    # - True in production (strong JWT_SECRET that is not the default placeholder)
    is_production = (
        bool(getattr(settings, "JWT_SECRET", None))
        and len(settings.JWT_SECRET) >= 32
        and not settings.JWT_SECRET.startswith("your_jwt")
    )

    response.set_cookie(
        key=ACCESS_TOKEN_COOKIE,
        value=access_token,
        httponly=True,
        secure=is_production,          # ← Bug #11 fix
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
    """
    Authenticate with email + password (OAuth2 form fields: username, password).
    Sets HttpOnly cookie for browser SSR and returns JSON token for APIs.
    """
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