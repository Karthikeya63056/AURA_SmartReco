import logging
from typing import Optional
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from app.models.user import User
from app.schemas.user import UserCreate
from app.core.security import get_password_hash, verify_password

logger = logging.getLogger(__name__)


def get_user_by_email(db: Session, email: str) -> Optional[User]:
    """Retrieve user by email address (case-insensitive)."""
    return db.query(User).filter(func.lower(User.email) == email.strip().lower()).first()


def register_user(db: Session, user_in: UserCreate) -> User:
    """Register a new user account."""
    email = user_in.email.strip().lower()
    existing = get_user_by_email(db, email)
    if existing:
        raise ValueError("Email already registered")

    user = User(
        email=email,
        full_name=user_in.full_name,
        hashed_password=get_password_hash(user_in.password),
        is_admin=False
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise ValueError("Email already registered")
    db.refresh(user)
    return user


def authenticate_user(db: Session, email: str, password: str) -> Optional[User]:
    """Authenticate email and password.

    Password is checked BEFORE is_active so deactivated accounts are
    indistinguishable from wrong-password attempts. Raises
    ValueError("This account has been deactivated.") only when the
    password was actually correct.
    """
    user = get_user_by_email(db, email)
    if not user:
        return None
    if not verify_password(password, user.hashed_password):
        return None
    if not user.is_active:
        logger.info(f"Login rejected for deactivated account {user.email}")
        raise ValueError("This account has been deactivated.")
    return user
