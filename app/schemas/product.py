from datetime import datetime
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field


class ProductBase(BaseModel):
    title: str
    category: str
    level: str
    price: float = Field(default=0.0, ge=0.0)
    rating: float = Field(default=4.5, ge=0.0, le=5.0)
    description: str
    tags: List[str] = Field(default_factory=list)
    syllabus: Optional[List[str]] = Field(default_factory=list)
    metadata_json: Optional[Dict[str, Any]] = Field(default_factory=dict)
    is_popular: bool = False
    is_trending: bool = False


class ProductCreate(ProductBase):
    pass


class ProductUpdate(BaseModel):
    title: Optional[str] = None
    category: Optional[str] = None
    level: Optional[str] = None
    price: Optional[float] = Field(default=None, ge=0.0)
    rating: Optional[float] = Field(default=None, ge=0.0, le=5.0)
    description: Optional[str] = None
    tags: Optional[List[str]] = None
    syllabus: Optional[List[str]] = None
    metadata_json: Optional[Dict[str, Any]] = None
    is_popular: Optional[bool] = None
    is_trending: Optional[bool] = None


class ProductResponse(ProductBase):
    id: int
    needs_reindex: bool
    created_at: datetime

    class Config:
        from_attributes = True
