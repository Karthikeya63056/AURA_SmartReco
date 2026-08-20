from datetime import datetime, timezone
from sqlalchemy import Column, Integer, String, Float, Text, JSON, Boolean, DateTime
from app.core.database import Base


class Product(Base):
    __tablename__ = "products"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String, index=True, nullable=False)
    category = Column(String, index=True, nullable=False)
    level = Column(String, index=True, nullable=False)  # Beginner, Intermediate, Advanced
    price = Column(Float, nullable=False, default=0.0)
    rating = Column(Float, nullable=False, default=4.5)
    description = Column(Text, nullable=False)
    tags = Column(JSON, nullable=False, default=list)  # ["python", "ai", "langgraph"]
    prerequisites = Column(JSON, nullable=True, default=list)  # ["Python Basics", "Data Structures"]
    skills_taught = Column(JSON, nullable=True, default=list)  # ["Machine Learning", "Model Evaluation"]
    syllabus = Column(JSON, nullable=True, default=list)
    metadata_json = Column(JSON, nullable=True, default=dict)
    needs_reindex = Column(Boolean, default=False, nullable=False)
    is_popular = Column(Boolean, default=False, nullable=False)
    is_trending = Column(Boolean, default=False, nullable=False)
    # Rev5: dual-write provenance. embedding_hash = hash of embedded text;
    # payload_hash = hash of pushed metadata. Used by outbox reconciler to detect drift.
    embedding_hash = Column(String(64), nullable=True)
    payload_hash = Column(String(64), nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), nullable=False)