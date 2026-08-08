from datetime import datetime, timezone
from sqlalchemy import Column, Integer, String, Text, JSON, Boolean, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from app.core.database import Base


class Recommendation(Base):
    __tablename__ = "recommendations"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    narrative = Column(Text, nullable=False)
    product_ids_json = Column(JSON, nullable=False, default=list)  # [1, 5, 8]
    product_reasons = Column(JSON, nullable=True, default=list)    # ["reason 1", "reason 2"]
    quality_score = Column(Integer, nullable=False, default=80)
    trigger_reason = Column(String, nullable=False)  # cold_start, high_intent, event_threshold, search_signal, staleness, manual
    is_active = Column(Boolean, default=True, nullable=False)
    refetch_count = Column(Integer, default=0, nullable=False)
    metadata_json = Column(JSON, nullable=True, default=dict)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False, index=True)

    # Relationships
    user = relationship("User", back_populates="recommendations")
