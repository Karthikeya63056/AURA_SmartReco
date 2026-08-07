import os
import logging
from contextlib import asynccontextmanager
from typing import Any, Dict, Optional

from fastapi import FastAPI, Request, Depends, status, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session
from sqlalchemy import or_
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.database import engine, Base, get_db
from app.dependencies import get_current_user_optional, get_admin_user
from app.models.product import Product
from app.models.user import User
from app.models.event import Event
from app.models.recommendation import Recommendation
from app.models.user_profile import UserProfile
from app.routers import auth, products, events, recommendations, admin
from app.scheduler.daily_digest import start_scheduler
from app.services.product_service import list_products

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


# Event types that count as "course engagement" for dashboard stats
COURSE_VIEW_EVENT_TYPES = (
    "course_view",
    "course_click",
    "course_impression",
)


def recommendation_to_dict(rec: Optional[Recommendation]) -> Optional[Dict[str, Any]]:
    """
    Normalize ORM Recommendation → template-safe dict.
    Templates expect product_ids (not product_ids_json).
    """
    if not rec:
        return None
    return {
        "id": rec.id,
        "narrative": rec.narrative or "",
        "product_ids": rec.product_ids_json or [],
        "quality_score": rec.quality_score,
        "trigger_reason": rec.trigger_reason,
        "refetch_count": getattr(rec, "refetch_count", 0) or 0,
        "metadata_json": getattr(rec, "metadata_json", None) or {},
        "created_at": rec.created_at,
    }


def build_user_stats(db: Session, user_id: int) -> Dict[str, int]:
    return {
        "courses_viewed": db.query(Event)
        .filter(
            Event.user_id == user_id,
            Event.event_type.in_(COURSE_VIEW_EVENT_TYPES),
        )
        .count(),
        "events": db.query(Event).filter(Event.user_id == user_id).count(),
        "recommendations": db.query(Recommendation)
        .filter(Recommendation.user_id == user_id)
        .count(),
    }


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifecycle: create tables, reindex, start scheduler."""
    logger.info("Initializing SmartReco 2026 Database Tables...")
    Base.metadata.create_all(bind=engine)

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

    yield
    logger.info("Shutting down SmartReco 2026 application...")


app = FastAPI(
    title="SmartReco 2026 — Agentic Course Recommendation System",
    description="Educational course platform with LangGraph self-correcting agent and Mesh API gateway compliance.",
    version="1.0.0",
    lifespan=lifespan,
)

# Register security headers middleware
app.add_middleware(SecurityHeadersMiddleware)

os.makedirs("app/static", exist_ok=True)
app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")

app.include_router(auth.router)
app.include_router(products.router)
app.include_router(events.router)
app.include_router(recommendations.router)
app.include_router(admin.router)


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

    return templates.TemplateResponse(
        "pages/dashboard.html",
        {
            "request": request,
            "user": user,
            "courses": courses,
            "recommendation": recommendation_to_dict(rec_row),
            "stats": build_user_stats(db, user.id),
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

    return templates.TemplateResponse(
        "pages/course_detail.html",
        {
            "request": request,
            "user": user,
            "course": course,
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
        },
    )


@app.get("/search", response_class=HTMLResponse)
def page_search(
    request: Request,
    q: str = "",
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
    user: User = Depends(get_admin_user),
):
    product_count = db.query(Product).count()
    event_count = db.query(Event).count()
    rec_count = db.query(Recommendation).count()

    recent_events = (
        db.query(Event).order_by(Event.created_at.desc()).limit(10).all()
    )

    return templates.TemplateResponse(
        "admin/dashboard.html",
        {
            "request": request,
            "user": user,
            "product_count": product_count,
            "event_count": event_count,
            "rec_count": rec_count,
            "recent_events": recent_events,
        },
    )


@app.get("/admin/products", response_class=HTMLResponse)
def page_admin_manage_products(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_admin_user),
):
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
    user: User = Depends(get_admin_user),
):
    target_user = db.query(User).filter(User.id == user_id).first()

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