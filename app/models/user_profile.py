from datetime import datetime, timezone
from sqlalchemy import Column, Integer, String, JSON, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from app.core.database import Base


class UserProfile(Base):
    __tablename__ = "user_profiles"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, unique=True, index=True)
    interests_json = Column(JSON, nullable=False, default=list)  # ["Generative AI", "LangGraph"]
    skill_level = Column(String, nullable=False, default="Beginner")  # Beginner, Intermediate, Advanced
    intent = Column(String, nullable=False, default="Exploring")  # Learning, Career Transition, Upskilling
    behavior_hash = Column(String, nullable=True)
    last_calculated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    # Relationships
    user = relationship("User", back_populates="profile")
