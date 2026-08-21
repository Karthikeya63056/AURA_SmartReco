"""End-to-end smoke test: boots the real app (lifespan included) and exercises
the critical paths fixed in the audit. Run from project root."""
import os
import sys

os.environ["DATABASE_URL"] = "sqlite:///./smoke_test.db"
os.environ["TESTING"] = "1"
os.environ["DEBUG"] = "true"  # plain-HTTP testserver: cookies must not be Secure
if not os.environ.get("JWT_SECRET"):
    os.environ["JWT_SECRET"] = "smoke_secret_key_for_verification_only_0123456789"

from fastapi.testclient import TestClient
from app.main import app

failures = []


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name} {detail}")
    if not cond:
        failures.append(name)


with TestClient(app) as c:
    # --- Public pages ---
    r = c.get("/")
    check("GET / (landing)", r.status_code == 200)
    r = c.get("/login")
    check("GET /login", r.status_code == 200)
    r = c.get("/catalog")
    check("GET /catalog", r.status_code == 200)

    # --- CSRF cookie issued on GET ---
    csrf = c.cookies.get("csrf_token")
    check("CSRF cookie set on GET", bool(csrf))

    # --- Register -> auto state ---
    email = f"smoke_{int(__import__('time').time())}@example.com"
    r = c.post("/auth/register", json={"email": email, "password": "supersecret123", "full_name": "Smoke Tester"})
    check("POST /auth/register", r.status_code == 201, str(r.status_code))

    # --- Login (no stale-cookie trap): fresh client, no cookies ---
    c2 = TestClient(app)
    r = c2.post("/auth/login", data={"username": email, "password": "supersecret123"})
    check("POST /auth/login (form)", r.status_code == 200, str(r.status_code))
    check("Login sets access_token cookie", "access_token" in c2.cookies)
    auth_headers = {"Authorization": f"Bearer {r.json()['access_token']}"}

    # --- Cookie-auth POST without CSRF header must be rejected ---
    r = c2.post(
        "/api/events/batch",
        json={"events": [{"session_id": "smoke_s1", "event_type": "course_view", "payload_json": {"course_id": 1}}]},
    )
    check("Cookie POST without CSRF header -> 403", r.status_code == 403, str(r.status_code))
    check("403 still issues fresh csrf cookie", bool(c2.cookies.get("csrf_token")))

    # --- Same POST with header passes ---
    hdr = {"X-CSRF-Token": c2.cookies.get("csrf_token", "")}
    r = c2.post(
        "/api/events/batch",
        json={"events": [
            {"session_id": "smoke_s1", "event_type": "course_view", "payload_json": {"course_id": 1}},
            {"session_id": "smoke_s1", "event_type": "search", "payload_json": {"query": "python"}},
            {"session_id": "smoke_s1", "event_type": "wishlist", "payload_json": {"course_id": 2}},
            {"session_id": "smoke_s1", "event_type": "syllabus_view", "payload_json": {"course_id": 2}},
            {"session_id": "smoke_s1", "event_type": "enroll_preview", "payload_json": {"course_id": 2}},
            {"session_id": "smoke_s2", "event_type": "page_view", "payload_json": {}},
        ]},
        headers=hdr,
    )
    check("Cookie POST with CSRF header -> 202 (mixed sessions ok)", r.status_code == 202, f"{r.status_code} {r.text[:120]}")

    # --- Bearer auth exempt from CSRF ---
    r = c2.post(
        "/api/events/batch",
        json={"events": [{"session_id": "smoke_b", "event_type": "page_view", "payload_json": {}}]},
        headers={**auth_headers},
    )
    check("Bearer POST exempt from CSRF (not 403)", r.status_code != 403, str(r.status_code))

    # --- /auth/me works ---
    r = c2.get("/auth/me", headers=auth_headers)
    check("GET /auth/me", r.status_code == 200 and r.json()["email"] == email)

    # --- Recommendations: refresh (v1) + current poll (v2, 204 when none) ---
    r = c2.post("/api/recommendations/refresh", json={}, headers={**auth_headers, **hdr})
    check("POST /api/recommendations/refresh", r.status_code == 200, f"{r.status_code} {r.text[:150]}")
    r = c2.get("/api/recommendations/current", headers=auth_headers)
    check("GET /api/recommendations/current (200 or 204)", r.status_code in (200, 204), str(r.status_code))
    if r.status_code == 204:
        check("204 has empty body", len(r.content) == 0)

    # --- Wishlist toggle + DELETE (seed a product first: fresh smoke DB is empty) ---
    from app.core.database import SessionLocal
    from app.models.product import Product
    with SessionLocal() as s:
        if not s.query(Product).filter(Product.id == 1).first():
            s.add(Product(title="Smoke Course", category="Programming", level="Beginner",
                          price=0.0, rating=4.5, description="smoke"))
            s.commit()
    r = c2.post("/api/wishlist/1", headers=hdr)
    check("POST /api/wishlist/1 toggle", r.status_code == 200, str(r.status_code))
    r = c2.delete("/api/wishlist/1", headers=hdr)
    check("DELETE /api/wishlist/1", r.status_code == 200, str(r.status_code))

    # --- Admin agent-runs page renders (admin/base.html exists) ---
    with TestClient(app) as c3:
        r = c3.post("/auth/login", data={"username": "admin@smartreco.ai", "password": "admin123456"})
        admin_ok = r.status_code == 200
        if admin_ok:
            r = c3.get("/admin/agent-runs")
            check("GET /admin/agent-runs renders", r.status_code == 200, str(r.status_code))
        else:
            print("[SKIP] admin login (seeded admin absent in fresh smoke DB)")

    # --- API 404 returns JSON, not HTML ---
    r = c2.get("/api/definitely-not-a-route", headers=auth_headers)
    check("API 404 is JSON", r.status_code == 404 and "application/json" in r.headers.get("content-type", ""))

    # --- Security headers ---
    r = c2.get("/dashboard", follow_redirects=False)  # anon -> 302, header must be on THIS response
    check("Cache-Control no-store on /dashboard", r.headers.get("cache-control") == "no-store",
          repr(r.headers.get("cache-control")))
    check("CSP header present", "content-security-policy" in {k.lower() for k in r.headers.keys()})

    # --- Logout requires CSRF via cookie and works with it ---
    r = c2.post("/auth/logout", headers=hdr)
    check("POST /auth/logout (with CSRF)", r.status_code == 200, str(r.status_code))

print()
if failures:
    print(f"SMOKE FAILED: {len(failures)} -> {failures}")
    sys.exit(1)
print("SMOKE PASSED: all checks green")
