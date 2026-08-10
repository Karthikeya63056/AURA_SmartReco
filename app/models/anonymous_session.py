from datetime import datetime, timezone
from sqlalchemy import Column, String, Integer, DateTime, ForeignKey, Index
from sqlalchemy.orm import relationship
from app.core.database import Base


class AnonymousSession(Base):
    """Maps an anonymous browser session to its own isolated User row.

    This prevents all anonymous visitors from sharing one global guest
    profile (cross-user contamination of events + recommendations) while
    keeping the mapping stable across requests from the same browser.
    """

    __tablename__ = "anonymous_sessions"

    id = Column(String, primary_key=True)  # sha256 of the client session_id
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    last_seen_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False, index=True)

    user = relationship("User")

    __table_args__ = (
        Index("idx_anon_session_user", "user_id"),
    )