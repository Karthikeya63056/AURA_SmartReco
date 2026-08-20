from datetime import datetime, timezone
from sqlalchemy import Column, Integer, String, Boolean, Float, DateTime, Text, ForeignKey
from sqlalchemy.orm import relationship
from app.core.database import Base


class AgentRun(Base):
    __tablename__ = "agent_runs"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    profile_hash = Column(String(64), nullable=False)
    pending_profile_hash = Column(String(64), nullable=True)
    refresh_requested = Column(Boolean, default=False)
    follow_up_count = Column(Integer, default=0)
    status = Column(String(20), nullable=False, default="queued")
    recommendation_id = Column(Integer, ForeignKey("recommendations.id"), nullable=True)
    trigger_reason = Column(String(100), nullable=True)
    skip_reasons_json = Column(Text, nullable=True)
    candidate_scores_json = Column(Text, nullable=True)
    model_used = Column(String(100), nullable=True)
    tokens = Column(Integer, nullable=True)
    cost_usd = Column(Float, nullable=True)
    latency_ms = Column(Integer, nullable=True)
    retry_count = Column(Integer, default=0)
    degraded = Column(Boolean, default=False)
    lease_until = Column(DateTime(timezone=True), nullable=True)
    last_error = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), nullable=False)

    # Relationships
    user = relationship("User", back_populates="agent_runs")
    recommendation = relationship("Recommendation", backref="agent_runs")