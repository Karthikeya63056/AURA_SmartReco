from datetime import datetime, timezone
from sqlalchemy import Column, Integer, String, DateTime, Text, ForeignKey
from sqlalchemy.orm import relationship
from app.core.database import Base


class VectorOutbox(Base):
    __tablename__ = "vector_outbox"

    id = Column(Integer, primary_key=True, index=True)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    op = Column(String(20), nullable=False)
    embedding_hash = Column(String(64), nullable=True)
    payload_hash = Column(String(64), nullable=True)
    status = Column(String(20), nullable=False, default="pending")
    attempts = Column(Integer, default=0)
    next_attempt_at = Column(DateTime(timezone=True), nullable=True)
    lease_until = Column(DateTime(timezone=True), nullable=True)
    last_error = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), nullable=False)

    # Relationships
    product = relationship("Product", backref="outbox_entries")