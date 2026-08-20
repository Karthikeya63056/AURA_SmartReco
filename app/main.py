import os
# Suppress ChromaDB telemetry error logs
os.environ["ANONYMIZED_TELEMETRY"] = "False"

import asyncio  # Rev5: for flush loop task
import logging
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, Request, Depends, status, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session
from sqlalchemy import or_, func
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.database import engine, Base, get_db
from app.config import settings
from app.dependencies import get_current_user_optional, get_admin_user
from app.models.product import Product
from app.models.user import User
from app.models.event import Event
from app.models.recommendation import Recommendation
from app.models.user_profile import UserProfile
from app.models.wishlist import WishlistItem
from app.routers import auth, products, events, recommendations, admin, wishlist
from app.scheduler.daily_digest import start_scheduler
from app.services.product_service import list_products
from app.core import event_buffer  # Rev5: async event buffer

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("smartreco")


# ============================================================
# Security Headers Middleware (Bug #10)
# ============================================================
class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
        # Practical CSP for this app (self + CDNs used by frontend)
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://cdnjs.cloudflare.com; "
            "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://cdnjs.cloudflare.com https://fonts.googleapis.com; "
            "font-src 'self' https://fonts.gstatic.com https://cdn.jsdelivr.net; "
            "img-src 'self' data: https:; "
            "connect-src 'self' https://api.meshapi.ai; "
            "frame-ancestors 'none';"
        )
        return response


class MaxBodySizeMiddleware(BaseHTTPMiddleware):
    """Reject oversized write requests before their body is read into memory."""

    async def dispatch(self, request: Request, call_next):
        if request.method in ("POST", "PUT", "PATCH"):
            # Content-Length check
            content_length = request.headers.get("content-length")
            if content_length:
                try:
                    is_too_large = int(content_length) > 1_000_000
                except ValueError:
                    is_too_large = True
                if is_too_large:
                    return HTMLResponse("Payload too large", status_code=413)

            # Chunked transfer-encoding bypasses Content-Length entirely
            transfer_encoding = request.headers.get("transfer-encoding", "").lower()
            if "chunked" in transfer_encoding:
                return HTMLResponse("Chunked request bodies are not allowed", status_code=413)

        return await call_next(request)


def recommendation_to_dict(rec: Optional[Recommendation]) -> Optional[Dict[str, Any]]:
    """
    Normalize ORM Recommendation → template-safe dict.
    Templates expect product_ids (not product_ids_json).
    """
    if not rec:
        return None
    meta = getattr(rec, "metadata_json", None) or {}
    reasons = getattr(rec, "product_reasons", None)
    if reasons is None and isinstance(meta, dict):
        reasons = meta.get("product_reasons") or meta.get("reasons") or []
    return {
        "id": rec.id,
        "narrative": rec.narrative or "",
        "product_ids": rec.product_ids_json or [],
        "product_reasons": reasons or [],
        "quality_score": rec.quality_score,
        "trigger_reason": rec.trigger_reason,
        "refetch_count": getattr(rec, "refetch_count", 0) or 0,
        "metadata_json": meta,
        "created_at": rec.created_at,
    }


def build_user_stats(db: Session, user_id: int) -> Dict[str, int]:
    viewed_ids = set()
    rows = db.query(Event.payload_json).filter(
        Event.user_id == user_id,
        Event.event_type == "course_view",
    ).all()
    for (payload,) in rows:
        if isinstance(payload, dict):
            course_id = payload.get("course_id")
            if course_id is not None:
                try:
                    viewed_ids.add(int(course_id))
                except (TypeError, ValueError):
                    pass
    return {
        "courses_viewed": len(viewed_ids),
        "events": db.query(Event).filter(Event.user_id == user_id).count(),
        "recommendations": db.query(Recommendation)
        .filter(Recommendation.user_id == user_id)
        .count(),
    }


def build_recently_viewed(db: Session, user_id: int, limit: int = 8) -> List[Dict[str, Any]]:
    """
    Last unique course_view events for the user, newest first.
    Returns template-safe dicts with product fields when the course still exists.
    """
    events = (
        db.query(Event)
        .filter(
            Event.user_id == user_id,
            Event.event_type == "course_view",
        )
        .order_by(Event.created_at.desc())
        .limit(40)
        .all()
    )

    seen = set()
    ordered_ids: List[int] = []
    titles_fallback: Dict[int, str] = {}
    viewed_at: Dict[int, Any] = {}

    for ev in events:
        payload = ev.payload_json if isinstance(ev.payload_json, dict) else {}
        raw_id = payload.get("course_id")
        if raw_id is None:
            continue
        try:
            cid = int(raw_id)
        except (TypeError, ValueError):
            continue
        if cid in seen:
            continue
        seen.add(cid)
        ordered_ids.append(cid)
        titles_fallback[cid] = str(payload.get("title") or "")
        viewed_at[cid] = ev.created_at
        if len(ordered_ids) >= limit:
            break

    if not ordered_ids:
        return []

    products = (
        db.query(Product)
        .filter(Product.id.in_(ordered_ids))
        .all()
    )
    by_id = {p.id: p for p in products}

    result: List[Dict[str, Any]] = []
    for cid in ordered_ids:
        product = by_id.get(cid)
        if product:
            result.append(
                {
                    "id": product.id,
                    "title": product.title,
                    "category": product.category,
                    "level": product.level,
                    "price": product.price,
                    "rating": product.rating,
                    "description": product.description,
                    "skills_taught": product.skills_taught or [],
                    "tags": product.tags or [],
                    "metadata_json": product.metadata_json or {},
                    "viewed_at": viewed_at.get(cid),
                }
            )
        else:
            # Course removed but still show a trail entry
            result.append(
                {
                    "id": cid,
                    "title": titles_fallback.get(cid) or f"Course #{cid}",
                    "category": "",
                    "level": "",
                    "price": None,
                    "rating": None,
                    "description": "",
                    "skills_taught": [],
                    "tags": [],
                    "metadata_json": {},
                    "viewed_at": viewed_at.get(cid),
                }
            )
    return result


# ============================================================
# Signal counts — all-time totals of the meaningful behavioral
# signals that drive the trigger engine and the recommendation
# agent. One GROUP BY query, zero N+1, noise-filtered.
# ============================================================
SIGNAL_EVENT_TYPES = (
    "course_view", "search", "course_click",
    "wishlist", "syllabus_view", "faq_expand",
)


def build_signal_counts(db: Session, user_id: int) -> Dict[str, int]:
    """All-time per-type counts of the meaningful behavioral signals."""
    counts = {t: 0 for t in SIGNAL_EVENT_TYPES}
    rows = (
        db.query(Event.event_type, func.count(Event.id))
        .filter(Event.user_id == user_id, Event.event_type.in_(SIGNAL_EVENT_TYPES))
        .group_by(Event.event_type)
        .all()
    )
    for etype, n in rows:
        counts[etype] = n
    return counts


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifecycle: create tables, reindex, start scheduler, start event buffer."""
    Base.metadata.create_all(bind=engine)

    try:
        from sqlalchemy import text
        with engine.connect() as conn:
            try:
                conn.execute(text("ALTER TABLE recommendations ADD COLUMN product_reasons JSON"))
                conn.commit()
                logger.info("Migrated recommendations table: added product_reasons column.")
            except Exception:
                pass

            try:
                conn.execute(text("ALTER TABLE products ADD COLUMN prerequisites JSON"))
                conn.commit()
                logger.info("Migrated products table: added prerequisites column.")
            except Exception:
                pass

            try:
                conn.execute(text("ALTER TABLE products ADD COLUMN skills_taught JSON"))
                conn.commit()
                logger.info("Migrated products table: added skills_taught column.")
            except Exception:
                pass

            try:
                conn.execute(text("ALTER TABLE users ADD COLUMN is_active BOOLEAN DEFAULT 1 NOT NULL"))
                conn.commit()
                logger.info("Migrated users table: added is_active column.")
            except Exception:
                pass
    except Exception:
        # Columns already exist or tables freshly created
        pass

    try:
        from app.services.product_service import reindex_needs_reindex_products

        reindexed = reindex_needs_reindex_products()
        if reindexed:
            logger.info(f"Startup reindex recovered {reindexed} product(s)")
    except Exception as e:
        logger.warning(f"Startup reindex failed (will retry on next boot): {e}")

    logger.info("Starting APScheduler Daily Digest Background Job...")
    try:
        start_scheduler()
    except Exception as e:
        logger.warning(f"Scheduler start skipped or already active: {str(e)}")

    # Rev5: Start event buffer flush loop (C1 — on app loop)
    flush_task = asyncio.create_task(event_buffer.flush_loop())
    logger.info("[Lifespan] Event buffer flush loop started")

    yield

    # Rev5: Cancel flush loop on shutdown
    flush_task.cancel()
    try:
        await flush_task
    except asyncio.CancelledError:
        pass
    logger.info("[Lifespan] Event buffer flush loop stopped")
    logger.info("Shutting down SmartReco 2026 application...")


app = FastAPI(
    title="SmartReco 2026 — Agentic Course Recommendation System",
    description="Educational course platform with LangGraph self-correcting agent and Mesh API gateway compliance.",
    version="1.0.0",
    lifespan=lifespan,
)

# Register security headers middleware
_allowed_origins = [
    origin.strip()
    for origin in settings.CORS_ORIGINS.split(",")
    if origin.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(MaxBodySizeMiddleware)

os.makedirs("app/static", exist_ok=True)
app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")

app.include_router(auth.router)
app.include_router(products.router)
app.include_router(events.router)
app.include_router(recommendations.router)
app.include_router(admin.router)
app.include_router(wishlist.router)


# ============================================================
# Frontend Page Routes
# ============================================================

@app.get("/", response_class=HTMLResponse)
def page_homepage(
    request: Request,
    db: Session = Depends(get_db),
    user: Optional[User] = Depends(get_current_user_optional),
):
    """Root: logged-in users → dashboard; others → landing."""
    if user:
        return RedirectResponse(url="/dashboard", status_code=302)

    courses = list_products(db=db, limit=4)
    return templates.TemplateResponse(
        "pages/landing.html",
        {
            "request": request,
            "user": user,
            "courses": courses,
            "recommendation": None,
        },
    )


@app.get("/landing", response_class=HTMLResponse)
def page_landing(
    request: Request,
    db: Session = Depends(get_db),
    user: Optional[User] = Depends(get_current_user_optional),
):
    courses = list_products(db=db, limit=4)
    return templates.TemplateResponse(
        "pages/landing.html",
        {
            "request": request,
            "user": user,
            "courses": courses,
            "recommendation": None,
        },
    )


@app.get("/dashboard", response_class=HTMLResponse)
def page_dashboard(
    request: Request,
    db: Session = Depends(get_db),
    user: Optional[User] = Depends(get_current_user_optional),
):
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    courses = list_products(db=db, limit=4)

    rec_row = (
        db.query(Recommendation)
        .filter(
            Recommendation.user_id == user.id,
            Recommendation.is_active == True,  # noqa: E712
        )
        .order_by(Recommendation.created_at.desc())
        .first()
    )

    recently_viewed = build_recently_viewed(db, user.id, limit=8)

    return templates.TemplateResponse(
        "pages/dashboard.html",
        {
            "request": request,
            "user": user,
            "courses": courses,
            "recommendation": recommendation_to_dict(rec_row),
            "stats": build_user_stats(db, user.id),
            "recently_viewed": recently_viewed,
        },
    )


@app.get("/catalog", response_class=HTMLResponse)
def page_catalog(
    request: Request,
    db: Session = Depends(get_db),
    user: Optional[User] = Depends(get_current_user_optional),
):
    courses = list_products(db=db, limit=50)
    return templates.TemplateResponse(
        "pages/catalog.html",
        {
            "request": request,
            "user": user,
            "courses": courses,
        },
    )


@app.get("/course/{course_id}", response_class=HTMLResponse)
def page_course_detail(
    course_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: Optional[User] = Depends(get_current_user_optional),
):
    course = db.query(Product).filter(Product.id == course_id).first()
    if not course:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Course not found")

    in_wishlist = False
    if user:
        in_wishlist = (
            db.query(WishlistItem)
            .filter(
                WishlistItem.user_id == user.id,
                WishlistItem.product_id == course.id,
            )
            .first()
            is not None
        )

    return templates.TemplateResponse(
        "pages/course_detail.html",
        {
            "request": request,
            "user": user,
            "course": course,
            "in_wishlist": in_wishlist,
        },
    )


@app.get("/paths", response_class=HTMLResponse)
def page_paths(
    request: Request,
    user: Optional[User] = Depends(get_current_user_optional),
):
    return templates.TemplateResponse(
        "pages/paths.html",
        {
            "request": request,
            "user": user,
        },
    )


@app.get("/about", response_class=HTMLResponse)
def page_about(
    request: Request,
    user: Optional[User] = Depends(get_current_user_optional),
):
    return templates.TemplateResponse(
        "pages/about.html",
        {
            "request": request,
            "user": user,
        },
    )


@app.get("/wishlist", response_class=HTMLResponse)
def page_wishlist(
    request: Request,
    db: Session = Depends(get_db),
    user: Optional[User] = Depends(get_current_user_optional),
):
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    items = (
        db.query(WishlistItem)
        .filter(WishlistItem.user_id == user.id)
        .order_by(WishlistItem.created_at.desc())
        .all()
    )
    courses = []
    if items:
        product_ids = [it.product_id for it in items]
        products_by_id = {
            p.id: p
            for p in db.query(Product).filter(Product.id.in_(product_ids)).all()
        }
        for it in items:
            p = products_by_id.get(it.product_id)
            if p:
                courses.append(p)

    return templates.TemplateResponse(
        "pages/wishlist.html",
        {"request": request, "user": user, "courses": courses},
    )


@app.get("/profile", response_class=HTMLResponse)
def page_profile(
    request: Request,
    db: Session = Depends(get_db),
    user: Optional[User] = Depends(get_current_user_optional),
):
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    rec_row = (
        db.query(Recommendation)
        .filter(
            Recommendation.user_id == user.id,
            Recommendation.is_active == True,  # noqa: E712
        )
        .order_by(Recommendation.created_at.desc())
        .first()
    )

    user_profile = (
        db.query(UserProfile).filter(UserProfile.user_id == user.id).first()
    )

    recent_events = (
        db.query(Event)
        .filter(Event.user_id == user.id)
        .order_by(Event.created_at.desc())
        .limit(10)
        .all()
    )

    return templates.TemplateResponse(
        "pages/profile.html",
        {
            "request": request,
            "user": user,
            "recommendation": recommendation_to_dict(rec_row),
            "user_profile": user_profile,
            "recent_events": recent_events,
            "stats": build_user_stats(db, user.id),
            "signals": build_signal_counts(db, user.id),
        },
    )


@app.get("/search", response_class=HTMLResponse)
def page_search(
    request: Request,
    q: str = Query(default="", max_length=200),
    db: Session = Depends(get_db),
    user: Optional[User] = Depends(get_current_user_optional),
):
    courses = []
    if q.strip():
        courses = (
            db.query(Product)
            .filter(
                or_(
                    Product.title.ilike(f"%{q}%"),
                    Product.description.ilike(f"%{q}%"),
                    Product.category.ilike(f"%{q}%"),
                )
            )
            .limit(20)
            .all()
        )

    return templates.TemplateResponse(
        "pages/search.html",
        {
            "request": request,
            "user": user,
            "query": q,
            "courses": courses,
        },
    )


@app.get("/login", response_class=HTMLResponse)
def page_login(
    request: Request,
    user: Optional[User] = Depends(get_current_user_optional),
):
    if user:
        return RedirectResponse(url="/dashboard", status_code=302)
    return templates.TemplateResponse(
        "pages/login.html",
        {
            "request": request,
            "user": user,
        },
    )


@app.get("/register", response_class=HTMLResponse)
def page_register(
    request: Request,
    user: Optional[User] = Depends(get_current_user_optional),
):
    if user:
        return RedirectResponse(url="/dashboard", status_code=302)
    return templates.TemplateResponse(
        "pages/register.html",
        {
            "request": request,
            "user": user,
        },
    )


# ============================================================
# Admin Pages (require admin)
# ============================================================

@app.get("/admin/dashboard", response_class=HTMLResponse)
def page_admin_dashboard(
    request: Request,
    db: Session = Depends(get_db),
    user: Optional[User] = Depends(get_current_user_optional),
):
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    if not user.is_admin:
        return RedirectResponse(url="/dashboard", status_code=302)

    product_count = db.query(Product).count()
    event_count = db.query(Event).count()
    rec_count = db.query(Recommendation).count()

    recent_events = (
        db.query(Event).order_by(Event.created_at.desc()).limit(10).all()
    )

    # Compute recommendation outcome metrics
    from app.routers.admin import _compute_recommendation_outcomes
    outcomes = _compute_recommendation_outcomes()

    return templates.TemplateResponse(
        "admin/dashboard.html",
        {
            "request": request,
            "user": user,
            "product_count": product_count,
            "event_count": event_count,
            "rec_count": rec_count,
            "recent_events": recent_events,
            "total_clicks": outcomes["total_clicks"],
            "total_dismisses": outcomes["total_dismisses"],
            "overall_ctr": outcomes["overall_ctr"],
            "rec_metrics": outcomes["rec_metrics"],
        },
    )


@app.get("/admin/products", response_class=HTMLResponse)
def page_admin_manage_products(
    request: Request,
    db: Session = Depends(get_db),
    user: Optional[User] = Depends(get_current_user_optional),
):
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    if not user.is_admin:
        return RedirectResponse(url="/dashboard", status_code=302)

    products_list = db.query(Product).order_by(Product.id.desc()).all()
    return templates.TemplateResponse(
        "admin/products.html",
        {
            "request": request,
            "user": user,
            "products": products_list,
        },
    )


@app.get("/admin/trace/{user_id}", response_class=HTMLResponse)
def page_admin_trace(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: Optional[User] = Depends(get_current_user_optional),
):
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    if not user.is_admin:
        return RedirectResponse(url="/dashboard", status_code=302)

    target_user = db.query(User).filter(User.id == user_id).first()
    if target_user is None:
        raise HTTPException(status_code=404, detail="User not found")

    rec_row = (
        db.query(Recommendation)
        .filter(Recommendation.user_id == user_id)
        .order_by(Recommendation.created_at.desc())
        .first()
    )

    recent_events = (
        db.query(Event)
        .filter(Event.user_id == user_id)
        .order_by(Event.created_at.desc())
        .limit(15)
        .all()
    )

    return templates.TemplateResponse(
        "admin/trace.html",
        {
            "request": request,
            "user": user,
            "user_id": user_id,
            "target_user": target_user,
            "recommendation": recommendation_to_dict(rec_row),
            "recent_events": recent_events,
        },
    )


# ============================================================
# Error handlers
# ============================================================

@app.exception_handler(404)
async def not_found_handler(request: Request, exc):
    return templates.TemplateResponse(
        "pages/404.html",
        {"request": request, "user": None},
        status_code=404,
    )


@app.exception_handler(500)
async def server_error_handler(request: Request, exc):
    logger.exception("Unhandled server error: %s", exc)
    return templates.TemplateResponse(
        "pages/500.html",
        {"request": request, "user": None},
        status_code=500,
    )


# ============================================================
# Entry point
# ============================================================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)