import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.dependencies import get_current_user_optional
from app.models.product import Product
from app.models.user import User
from app.models.wishlist import WishlistItem

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/wishlist", tags=["Wishlist"])


@router.post("/{product_id}")
def toggle_wishlist(
    product_id: int,
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    """Add if absent, remove if present. One endpoint = simple client logic."""
    if not current_user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sign in to manage your wishlist",
        )

    product = db.query(Product).filter(Product.id == product_id).first()
    if not product:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Course not found")

    existing = (
        db.query(WishlistItem)
        .filter(
            WishlistItem.user_id == current_user.id,
            WishlistItem.product_id == product_id,
        )
        .first()
    )

    if existing:
        db.delete(existing)
        db.commit()
        added = False
    else:
        db.add(WishlistItem(user_id=current_user.id, product_id=product_id))
        try:
            db.commit()
            added = True
        except IntegrityError:
            # Concurrent toggle raced: another request already inserted the same
            # (user, product) row. Treat as "already present" instead of 500.
            db.rollback()
            added = False

    count = (
        db.query(WishlistItem)
        .filter(WishlistItem.user_id == current_user.id)
        .count()
    )
    return {"added": added, "count": count}


@router.delete("/{product_id}")
def remove_from_wishlist(
    product_id: int,
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    """Explicit removal (distinct from the toggle POST)."""
    if not current_user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sign in to manage your wishlist",
        )

    deleted = (
        db.query(WishlistItem)
        .filter(
            WishlistItem.user_id == current_user.id,
            WishlistItem.product_id == product_id,
        )
        .delete(synchronize_session=False)
    )
    db.commit()
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Course not in wishlist",
        )

    return {"status": "removed", "product_id": product_id, "added": False}