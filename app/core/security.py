from datetime import datetime, timedelta, timezone
from typing import Optional, Union, Any
from jose import jwt, JWTError
from passlib.context import CryptContext
import bcrypt as _bcrypt
from app.config import settings

# passlib 1.7.4 probes the bcrypt backend with >72-byte test vectors during
# backend load; bcrypt>=4.1 raises ValueError instead of silently truncating.
# Restore the silent-truncation contract passlib expects (real passwords are
# truncated explicitly in hash/verify below).
_orig_hashpw = _bcrypt.hashpw


def _hashpw_compat(password: bytes, salt: bytes) -> bytes:
    return _orig_hashpw(password[:72], salt)


_bcrypt.hashpw = _hashpw_compat

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a plain text password against its hash."""
    plain_password = plain_password.encode("utf-8")[:72].decode("utf-8", "ignore")
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    """Generate bcrypt hash for a password."""
    password = password.encode("utf-8")[:72].decode("utf-8", "ignore")
    return pwd_context.hash(password)


def create_access_token(
    subject: Union[str, Any],
    token_version: int,
    expires_delta: Optional[timedelta] = None,
) -> str:
    """
    Create a signed JWT access token with a `ver` claim tied to the user's
    current token_version. Bumping token_version instantly invalidates all
    JWTs issued before the bump (server-side revocation without a token store).
    """
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(
            minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES
        )

    to_encode = {
        "exp": expire,
        "iat": int(datetime.now(timezone.utc).timestamp()),
        "sub": str(subject),
        "ver": int(token_version),  # Rev5 G4.3: revocation claim
    }
    return jwt.encode(to_encode, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def decode_access_token(token: str) -> Optional[dict]:
    """Decode and validate a JWT access token. Returns None on any failure."""
    try:
        payload = jwt.decode(
            token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM]
        )
        return payload
    except JWTError:
        return None


def verify_token_version(decoded_payload: dict, user_token_version: int) -> bool:
    """
    Rev5 G4.3: The JWT's `ver` claim must exactly match the user's current
    token_version. Mismatch means the user has logged out / changed password
    since this token was issued.
    """
    if not decoded_payload:
        return False
    claim_ver = decoded_payload.get("ver")
    if claim_ver is None:
        # Legacy token issued before Rev5: reject to force re-authentication
        return False
    return int(claim_ver) == int(user_token_version)