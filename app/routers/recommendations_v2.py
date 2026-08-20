"""
Rev5 recommendations router with cache-first polling.
"""
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.cache import cache
from app.dependencies import get_current_user
from app.models.user import User
from app.models.recommendation import Recommendation
from app.models.product import Product

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/recommendations", tags=["Recommendations"])

CACHE_TTL_SECONDS = 900


@router.get("/current")
def get_current_recommendation(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Poll endpoint: returns the user's current active recommendation.
    
    Cache-first: hits in-memory TTL cache (900s). On miss, falls back to DB.
    Returns 204 No Content if no active recommendation exists.
    """
    cache_key = f"active_rec:{user.id}"
    
    # Try cache first
    cached = cache.get(cache_key)
    if cached is not None:
        # Load product details for the cached IDs
        product_ids = cached.get("product_ids", [])
        products = (
            db.query(Product)
            .filter(Product.id.in_(product_ids))
            .all()
            if product_ids else []
        )
        products_by_id = {p.id: p for p in products}
        
        return {
            "id": cached["id"],
            "narrative": cached["narrative"],
            "product_ids": product_ids,
            "product_reasons": cached.get("product_reasons", []),
            "quality_score": cached.get("quality_score"),
            "products": [
                {
                    "id": p.id,
                    "title": p.title,
                    "category": p.category,
                    "level": p.level,
                    "description": p.description,
                    "skills_taught": p.skills_taught or [],
                }
                for pid in product_ids
                if (p := products_by_id.get(pid)) is not None
            ],
            "cached": True,
        }
    
    # Cache miss: fall back to DB
    rec = (
        db.query(Recommendation)
        .filter(Recommendation.user_id == user.id, Recommendation.is_active == True)  # noqa: E712
        .order_by(Recommendation.created_at.desc())
        .first()
    )
    
    if rec is None:
        raise HTTPException(status_code=status.HTTP_204_NO_CONTENT, detail="No active recommendation")
    
    # Load product details
    product_ids = rec.product_ids_json or []
    products = (
        db.query(Product).filter(Product.id.in_(product_ids)).all()
        if product_ids else []
    )
    products_by_id = {p.id: p for p in products}
    
    # Populate cache for next poll
    rec_data = {
        "id": rec.id,
        "narrative": rec.narrative or "",
        "product_ids": product_ids,
        "product_reasons": getattr(rec, "product_reasons", []) or [],
        "quality_score": rec.quality_score,
    }
    cache.set(cache_key, rec_data, CACHE_TTL_SECONDS)
    
    return {
        "id": rec.id,
        "narrative": rec.narrative or "",
        "product_ids": product_ids,
        "product_reasons": getattr(rec, "product_reasons", []) or [],
        "quality_score": rec.quality_score,
        "products": [
            {
                "id": p.id,
                "title": p.title,
                "category": p.category,
                "level": p.level,
                "description": p.description,
                "skills_taught": p.skills_taught or [],
            }
            for pid in product_ids
            if (p := products_by_id.get(pid)) is not None
        ],
        "cached": False,
    }


@router.post("/dismiss/{recommendation_id}", status_code=status.HTTP_200_OK)
def dismiss_recommendation(
    recommendation_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Mark a recommendation as dismissed (deactivates it)."""
    rec = (
        db.query(Recommendation)
        .filter(
            Recommendation.id == recommendation_id,
            Recommendation.user_id == user.id,
        )
        .first()
    )
    if rec is None:
        raise HTTPException(status_code=404, detail="Recommendation not found")
    
    rec.is_active = False
    db.commit()
    
    # Invalidate cache so next poll sees no active rec
    cache_key = f"active_rec:{user.id}"
    cache.delete(cache_key)
    
    return {"status": "dismissed", "recommendation_id": recommendation_id}