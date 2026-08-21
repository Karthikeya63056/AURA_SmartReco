import os
os.environ["DATABASE_URL"] = "sqlite:///./smoke_test.db"
os.environ["TESTING"] = "1"
if not os.environ.get("JWT_SECRET"):
    os.environ["JWT_SECRET"] = "smoke_secret_key_for_verification_only_0123456789"

from fastapi.testclient import TestClient
from app.main import app
from app.config import settings

print("DEBUG setting:", settings.DEBUG)
import time
with TestClient(app) as c:
    email = f"dbg_{int(time.time())}@example.com"
    r = c.post("/auth/register", json={"email": email, "password": "supersecret123"})
    print("register:", r.status_code, "| cookies after:", dict(c.cookies))

    c2 = TestClient(app)
    r = c2.post("/auth/login", data={"username": email, "password": "supersecret123"})
    print("login:", r.status_code)
    print("login set-cookie:", r.headers.get("set-cookie"))
    print("c2 cookies:", dict(c2.cookies))

    r = c2.post("/api/events/batch",
                json={"events": [{"session_id": "d1", "event_type": "page_view", "payload_json": {}}]})
    print("batch no-header:", r.status_code, r.text[:100])

    hdr = {"X-CSRF-Token": c2.cookies.get("csrf_token", "")}
    r = c2.post("/api/wishlist/1", headers=hdr)
    print("wishlist toggle:", r.status_code, r.text[:150])

    r = c2.get("/dashboard")
    print("dashboard:", r.status_code, "| cache-control:", repr(r.headers.get("cache-control")))
