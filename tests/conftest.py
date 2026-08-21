import os

# Isolate tests from the dev DB and dev services BEFORE importing anything from app.
os.environ["DATABASE_URL"] = "sqlite:///./test_smartreco.db"
os.environ["TESTING"] = "1"
os.environ["JWT_SECRET"] = os.environ.get("JWT_SECRET", "test_secret_key_for_pytest_only_not_prod_0123456789")

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from fastapi.testclient import TestClient

from app.core.database import Base, get_db
from app.main import app

TEST_DATABASE_URL = "sqlite:///./test_smartreco.db"

engine = create_engine(TEST_DATABASE_URL, connect_args={"check_same_thread": False})
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


@pytest.fixture(scope="session", autouse=True)
def _ensure_schema():
    """Create the full schema once per session on the TEST database.

    Raw-SessionLocal tests (test_rev5_*) never trigger lifespan/create_all,
    so the schema must exist independently of fixture ordering.
    """
    Base.metadata.create_all(bind=engine)
    yield


@pytest.fixture(scope="function")
def db_session():
    """Fresh, empty tables for each test function.

    Tables are created once (see _ensure_schema) and NEVER dropped here —
    dropping would break later tests that use raw SessionLocal. Instead,
    every table is emptied in FK-safe order after each test.
    """
    Base.metadata.create_all(bind=engine)
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()
        with engine.begin() as conn:
            for table in reversed(Base.metadata.sorted_tables):
                try:
                    conn.execute(table.delete())
                except Exception:
                    pass


@pytest.fixture(scope="function")
def client(db_session):
    """FastAPI TestClient fixture."""
    def override_get_db():
        try:
            yield db_session
        finally:
            db_session.close()

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
