from datetime import datetime
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field


class EventBatchItem(BaseModel):
    session_id: str
    event_type: str = Field(description="page_view, search, click, wishlist, time_on_page, syllabus_view, enroll_preview")
    payload_json: Dict[str, Any] = Field(default_factory=dict)
    idempotency_key: Optional[str] = None
    created_at: Optional[datetime] = Field(default_factory=datetime.utcnow)


class EventBatchRequest(BaseModel):
    events: List[EventBatchItem]


class EventResponse(BaseModel):
    id: int
    user_id: int
    session_id: str
    event_type: str
    payload_json: Dict[str, Any]
    created_at: datetime

    class Config:
        from_attributes = True
