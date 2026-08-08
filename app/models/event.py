from datetime import datetime, timezone
from sqlalchemy import Column, Integer, String, JSON, DateTime, ForeignKey, Index
from sqlalchemy.orm import relationship
from app.core.database import Base


class Event(Base):
    __tablename__ = "events"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    session_id = Column(String, index=True, nullable=False)
    event_type = Column(String, index=True, nullable=False)  # page_view, search, click, wishlist, time_on_page
    payload_json = Column(JSON, nullable=False, default=dict)
    idempotency_key = Column(String, unique=True, index=True, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False, index=True)

    # Relationships
    user = relationship("User", back_populates="events")

    __table_args__ = (
        Index("idx_user_events_created", "user_id", "created_at"),
    )
