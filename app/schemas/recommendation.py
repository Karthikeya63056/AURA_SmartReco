from datetime import datetime
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field
from app.schemas.product import ProductResponse


class RecommendationResponse(BaseModel):
    id: int
    user_id: int
    narrative: str
    product_ids: List[int] = Field(alias="product_ids_json")
    products: List[ProductResponse] = Field(default_factory=list)
    quality_score: int
    trigger_reason: str
    is_active: bool
    created_at: datetime

    class Config:
        from_attributes = True
        populate_by_name = True
