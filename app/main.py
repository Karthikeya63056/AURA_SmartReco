import os
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Depends, status, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.exception_handlers import http_exception_handler
from sqlalchemy.orm import Session
from sqlalchemy import or_

from app.config import settings
from app.core.database import engine, Base, get_db
from app.dependencies import get_current_user_optional
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifecycle event handler for database tables creation and scheduler initialization."""
    logger.info("Initializing SmartReco 2026 Database Tables...")
    Base.metadata.create_all(bind=engine)

    # Recover products that failed dual-write (needs_reindex=True) on a prior run
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
    lifespan=lifespan
)

# Static files & Jinja2 Templates
os.makedirs("app/static", exist_ok=True)
app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")

# Include API Routers
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
    user: User = Depends(get_current_user_optional)
):
    """Root: redirect logged-in users to dashboard, others to landing."""
    if user:
        return RedirectResponse(url="/dashboard", status_code=302)

    courses = list_products(db=db, limit=4)
    return templates.TemplateResponse("landing.html", {
        "request": request,
        "user": user,
        "courses": courses
    })


@app.get("/landing", response_class=HTMLResponse)
def page_landing(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_optional)
):
    """Marketing / landing page."""
    courses = list_products(db=db, limit=4)
    return templates.TemplateResponse("landing.html", {
        "request": request,
        "user": user,
        "courses": courses
    })


@app.get("/dashboard", response_class=HTMLResponse)
def page_dashboard(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_optional)
):
    """Logged-in user dashboard with recommendation + stats."""
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    courses = list_products(db=db, limit=4)

    recommendation = (
        db.query(Recommendation)
        .filter(
            Recommendation.user_id == user.id,
            Recommendation.is_active == True
        )
        .order_by(Recommendation.created_at.desc())
        .first()
    )

    stats = {
        "courses_viewed": db.query(Event).filter(
            Event.user_id == user.id,
            Event.event_type.in_(["course_view", "course_click", "course_viewability"])
        ).count(),
        "events": db.query(Event).filter(Event.user_id == user.id).count(),
        "recommendations": db.query(Recommendation).filter(
            Recommendation.user_id == user.id
        ).count()
    }

    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "user": user,
        "courses": courses,
        "recommendation": recommendation,
        "stats": stats
    })


@app.get("/catalog", response_class=HTMLResponse)
def page_catalog(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_optional)
):
    """Full course catalog page."""
    courses = list_products(db=db, limit=50)
    return templates.TemplateResponse("catalog.html", {
        "request": request,
        "user": user,
        "courses": courses
    })


@app.get("/course/{course_id}", response_class=HTMLResponse)
def page_course_detail(
    course_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_optional)
):
    """Single course detail page."""
    course = db.query(Product).filter(Product.id == course_id).first()
    if not course:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Course not found")

    return templates.TemplateResponse("product_detail.html", {
        "request": request,
        "user": user,
        "course": course
    })


@app.get("/paths", response_class=HTMLResponse)
def page_paths(
    request: Request,
    user: User = Depends(get_current_user_optional)
):
    """Learning paths page."""
    return templates.TemplateResponse("paths.html", {
        "request": request,
        "user": user
    })


@app.get("/about", response_class=HTMLResponse)
def page_about(
    request: Request,
    user: User = Depends(get_current_user_optional)
):
    """How AURA works page."""
    return templates.TemplateResponse("about.html", {
        "request": request,
        "user": user
    })


@app.get("/profile", response_class=HTMLResponse)
def page_profile(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_optional)
):
    """User profile page with living profile + activity."""
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    recommendation = (
        db.query(Recommendation)
        .filter(
            Recommendation.user_id == user.id,
            Recommendation.is_active == True
        )
        .order_by(Recommendation.created_at.desc())
        .first()
    )

    user_profile = (
        db.query(UserProfile)
        .filter(UserProfile.user_id == user.id)
        .first()
    )

    recent_events = (
        db.query(Event)
        .filter(Event.user_id == user.id)
        .order_by(Event.created_at.desc())
        .limit(10)
        .all()
    )

    stats = {
        "courses_viewed": db.query(Event).filter(
            Event.user_id == user.id,
            Event.event_type.in_(["course_view", "course_click", "course_viewability"])
        ).count(),
        "events": db.query(Event).filter(Event.user_id == user.id).count(),
        "recommendations": db.query(Recommendation).filter(
            Recommendation.user_id == user.id
        ).count()
    }

    return templates.TemplateResponse("profile.html", {
        "request": request,
        "user": user,
        "recommendation": recommendation,
        "user_profile": user_profile,
        "recent_events": recent_events,
        "stats": stats
    })


@app.get("/search", response_class=HTMLResponse)
def page_search(
    request: Request,
    q: str = "",
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_optional)
):
    """Search results page."""
    courses = []
    if q.strip():
        courses = (
            db.query(Product)
            .filter(
                or_(
                    Product.title.ilike(f"%{q}%"),
                    Product.description.ilike(f"%{q}%"),
                    Product.category.ilike(f"%{q}%")
                )
            )
            .limit(20)
            .all()
        )

    return templates.TemplateResponse("search.html", {
        "request": request,
        "user": user,
        "query": q,
        "courses": courses
    })


@app.get("/login", response_class=HTMLResponse)
def page_login(
    request: Request,
    user: User = Depends(get_current_user_optional)
):
    """Login page."""
    if user:
        return RedirectResponse(url="/dashboard", status_code=302)
    return templates.TemplateResponse("login.html", {
        "request": request,
        "user": user
    })


@app.get("/register", response_class=HTMLResponse)
def page_register(
    request: Request,
    user: User = Depends(get_current_user_optional)
):
    """Registration page."""
    if user:
        return RedirectResponse(url="/dashboard", status_code=302)
    return templates.TemplateResponse("register.html", {
        "request": request,
        "user": user
    })


# ============================================================
# Admin Pages
# ============================================================

@app.get("/admin/dashboard", response_class=HTMLResponse)
def page_admin_dashboard(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_optional)
):
    """Admin analytics dashboard."""
    product_count = db.query(Product).count()
    event_count = db.query(Event).count()
    rec_count = db.query(Recommendation).count()

    return templates.TemplateResponse("admin/dashboard.html", {
        "request": request,
        "user": user,
        "product_count": product_count,
        "event_count": event_count,
        "rec_count": rec_count
    })


@app.get("/admin/products", response_class=HTMLResponse)
def page_admin_manage_products(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_optional)
):
    """Admin course catalog manager."""
    products = db.query(Product).all()
    return templates.TemplateResponse("admin/manage_products.html", {
        "request": request,
        "user": user,
        "products": products
    })


@app.get("/admin/trace/{user_id}", response_class=HTMLResponse)
def page_admin_trace(
    user_id: int,
    request: Request,
    user: User = Depends(get_current_user_optional)
):
    """Admin agent trace viewer."""
    return templates.TemplateResponse("admin/trace.html", {
        "request": request,
        "user": user
    })


# ============================================================
# 404 Handler
# ============================================================

@app.exception_handler(404)
async def not_found_handler(request: Request, exc):
    return templates.TemplateResponse(
        "404.html",
        {"request": request, "user": None},
        status_code=404
    )


# ============================================================
# Entry Point
# ============================================================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)