import os
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Depends, status, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.config import settings
from app.core.database import engine, Base, get_db
from app.dependencies import get_current_user_optional
from app.models.product import Product
from app.models.user import User
from app.models.event import Event
from app.models.recommendation import Recommendation
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


# Frontend Page Routes
@app.get("/", response_class=HTMLResponse)
def page_homepage(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_optional)
):
    """Render homepage dashboard with active recommendation narrative and course catalog."""
    courses = list_products(db=db, limit=6)
    return templates.TemplateResponse("index.html", {
        "request": request,
        "user": user,
        "courses": courses
    })


@app.get("/catalog", response_class=HTMLResponse)
def page_catalog(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_optional)
):
    """Render full course catalog page."""
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
    """Render single course detail page."""
    course = db.query(Product).filter(Product.id == course_id).first()
    if not course:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Course not found")

    return templates.TemplateResponse("product_detail.html", {
        "request": request,
        "user": user,
        "course": course
    })


@app.get("/login", response_class=HTMLResponse)
def page_login(request: Request, user: User = Depends(get_current_user_optional)):
    """Render login page."""
    return templates.TemplateResponse("login.html", {"request": request, "user": user})


@app.get("/register", response_class=HTMLResponse)
def page_register(request: Request, user: User = Depends(get_current_user_optional)):
    """Render registration page."""
    return templates.TemplateResponse("register.html", {"request": request, "user": user})


@app.get("/admin/dashboard", response_class=HTMLResponse)
def page_admin_dashboard(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_optional)
):
    """Render admin dashboard page."""
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
    """Render admin course catalog manager."""
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
    """Render agent trace viewer."""
    return templates.TemplateResponse("admin/trace.html", {
        "request": request,
        "user": user
    })


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
