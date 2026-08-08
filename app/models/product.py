from datetime import datetime
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
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
