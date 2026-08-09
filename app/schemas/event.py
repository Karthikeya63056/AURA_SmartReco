from datetime import datetime
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field, field_validator
import json


# Strict allowlist of supported event types
ALLOWED_EVENT_TYPES = {
    "page_view",
    "search",
    "click",
    "wishlist",
    "time_on_page",
    "syllabus_view",
    "enroll_preview",
    "course_click",
    "course_view",
    "course_impression",
    "rec_click",
    "rec_dismiss",
    "wishlist_remove",
    # High-value intent / exploration signals (Phase 1 frontend)
    "faq_expand",
    "instructor_view",
    "share",
}

MAX_PAYLOAD_BYTES = 5 * 1024  # 5 KB


class EventBatchItem(BaseModel):
    session_id: str = Field(..., min_length=1, max_length=128)
    event_type: str = Field(
        ...,
        description=(
            "One of: page_view, search, click, wishlist, time_on_page, "
            "syllabus_view, enroll_preview, course_click, course_view, "
            "course_impression, rec_click, rec_dismiss, faq_expand, "
            "instructor_view, share"
        ),
    )
    payload_json: Dict[str, Any] = Field(default_factory=dict)
    idempotency_key: Optional[str] = Field(default=None, max_length=128)
    created_at: Optional[datetime] = Field(default_factory=datetime.utcnow)

    @field_validator("event_type")
    @classmethod
    def validate_event_type(cls, v: str) -> str:
        if v not in ALLOWED_EVENT_TYPES:
            raise ValueError(
                f"Invalid event_type '{v}'. Allowed values: {sorted(ALLOWED_EVENT_TYPES)}"
            )
        return v

    @field_validator("payload_json")
    @classmethod
    def validate_payload_size(cls, v: Dict[str, Any]) -> Dict[str, Any]:
        try:
            serialized = json.dumps(v, separators=(",", ":"))
        except (TypeError, ValueError):
            raise ValueError("payload_json must be JSON-serializable")
        if len(serialized.encode("utf-8")) > MAX_PAYLOAD_BYTES:
            raise ValueError(f"payload_json exceeds maximum size of {MAX_PAYLOAD_BYTES} bytes")
        return v


class EventBatchRequest(BaseModel):
    events: List[EventBatchItem] = Field(..., max_length=50)


class EventResponse(BaseModel):
    id: int
    user_id: int
    session_id: str
    event_type: str
    payload_json: Dict[str, Any]
    created_at: datetime

    class Config:
        from_attributes = True