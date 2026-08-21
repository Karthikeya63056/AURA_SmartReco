import os
os.environ["DATABASE_URL"] = "sqlite:///./smoke_test.db"
os.environ["TESTING"] = "1"
os.environ["DEBUG"] = "true"
if not os.environ.get("JWT_SECRET"):
    os.environ["JWT_SECRET"] = "smoke_secret_key_for_verification_only_0123456789"

import time
from fastapi.testclient import TestClient
from app.main import app
from app.core.database import SessionLocal
from app.models.user import User
from app.core.security import get_password_hash

with SessionLocal() as s:
    if not s.query(User).filter(User.email == "admin@smartreco.ai").first():
        s.add(User(email="admin@smartreco.ai", hashed_password=get_password_hash("admin123456"),
                   full_name="Admin", is_admin=True))
        s.commit()

with TestClient(app) as c:
    r = c.post("/auth/login", data={"username": "admin@smartreco.ai", "password": "admin123456"})
    assert r.status_code == 200, r.text
    r = c.get("/admin/agent-runs")
    print("admin/agent-runs:", r.status_code)
    assert r.status_code == 200, r.text[:300]
    assert "Agent Runs" in r.text
    r = c.get("/admin/dashboard")
    print("admin/dashboard:", r.status_code)
    assert r.status_code == 200
    # Gate 5: admins must be skipped by the trigger (bug #93)
    from app.services.trigger_engine import evaluate_5gate_trigger
    with SessionLocal() as s:
        admin = s.query(User).filter(User.email == "admin@smartreco.ai").first()
        res = evaluate_5gate_trigger(s, admin.id, "sess_x")
        print("admin gate5 skip:", res.get("skip_reasons"))
        assert "user_inactive_or_admin" in res.get("skip_reasons", [])
print("ADMIN + GATE5 CHECKS PASSED")
