"""
Phase 4 exit-gate tests (G4.1–G4.5):
- G4.1 cache freshness (poll endpoint)
- G4.2 login lockout (5 fails / 15 min, 429 + Retry-After)
- G4.3 revocation (logout bumps token_version, old token rejected)
- G4.4 CSRF (cookie auth without header → 403)
- G4.5 digest dedupe (identical fingerprint → skip)
"""
import time
import pytest

from fastapi.testclient import TestClient

from app.main import app
from app.core.database import SessionLocal
from app.core.cache import cache
from app.core.security import create_access_token, decode_access_token, verify_token_version
from app.models.user import User
from app.models.recommendation import Recommendation
from app.services.digest import (
    compute_digest_fingerprint,
    is_duplicate_digest,
    record_digest_sent,
    should_send_digest,
)
from app.services.login_lockout import (
    check_lockout,
    record_failure,
    record_success,
    LOCKOUT_WINDOW_SECONDS,
    MAX_FAILS_PER_WINDOW,
)


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


# ============================================================
# G4.1: Cache freshness (poll endpoint)
# ============================================================

def test_g41_poll_endpoint_returns_cached_on_second_call(client, db):
    """G4.1: Second poll hit returns cached=True."""
    # Create a user + recommendation
    email = f"poll_cache_{int(time.time())}@test.com"
    user = User(email=email, hashed_password="x", full_name="Poll Tester")
    db.add(user)
    db.commit()
    db.refresh(user)

    rec = Recommendation(
        user_id=user.id,
        narrative="Test narrative for cache freshness",
        product_ids_json=[1, 2, 3],
        quality_score=85,
        trigger_reason="manual",
        is_active=True,
    )
    db.add(rec)
    db.commit()
    db.refresh(rec)

    # Create a valid token for this user
    token = create_access_token(subject=user.id, token_version=user.token_version)

    # First call — cache miss, populates cache
    r1 = client.get(
        "/api/recommendations/current",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r1.status_code == 200, f"First poll failed: {r1.text}"
    assert r1.json().get("cached") is False

    # Second call — cache hit
    r2 = client.get(
        "/api/recommendations/current",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r2.status_code == 200
    assert r2.json().get("cached") is True


# ============================================================
# G4.2: Login lockout
# ============================================================

def test_g42_lockout_after_max_failures():
    """G4.2: 5 failures within window → locked; success clears."""
    ip = "10.0.0.99"
    email = f"lockout_{int(time.time())}@test.com"

    # Initially unlocked
    locked, retry = check_lockout(ip, email)
    assert locked is False

    # Record MAX_FAILS_PER_WINDOW failures
    for _ in range(MAX_FAILS_PER_WINDOW):
        record_failure(ip, email)

    # Now locked
    locked, retry = check_lockout(ip, email)
    assert locked is True
    assert retry > 0

    # Success clears
    record_success(ip, email)
    locked, retry = check_lockout(ip, email)
    assert locked is False


def test_g42_lockout_email_and_ip_both_count():
    """G4.2: Lockout is keyed by BOTH IP and email."""
    ip_a = "10.0.0.100"
    ip_b = "10.0.0.101"
    email = f"dual_{int(time.time())}@test.com"

    # Lock by IP
    for _ in range(MAX_FAILS_PER_WINDOW):
        record_failure(ip_a, email)

    # Different IP, same email → should still be locked (email key)
    locked, _ = check_lockout(ip_b, email)
    assert locked is True, "Email lockout should block different IPs"


# ============================================================
# G4.3: Revocation (token_version)
# ============================================================

def test_g43_old_token_rejected_after_version_bump(db):
    """G4.3: Bumping token_version invalidates all previously-issued tokens."""
    email = f"revoke_{int(time.time())}@test.com"
    user = User(email=email, hashed_password="x", token_version=0)
    db.add(user)
    db.commit()
    db.refresh(user)

    # Issue a token at version 0
    old_token = create_access_token(subject=user.id, token_version=0)
    old_payload = decode_access_token(old_token)
    assert old_payload is not None
    assert verify_token_version(old_payload, user.token_version) is True

    # Bump version (simulates logout)
    user.token_version = 1
    db.commit()

    # Old token should now be rejected
    assert verify_token_version(old_payload, user.token_version) is False

    # New token at version 1 should pass
    new_token = create_access_token(subject=user.id, token_version=1)
    new_payload = decode_access_token(new_token)
    assert verify_token_version(new_payload, user.token_version) is True


def test_g43_deactivated_user_rejected(client, db):
    """G4.3: is_active=False → user is rejected even with valid token."""
    email = f"deactive_{int(time.time())}@test.com"
    user = User(email=email, hashed_password="x", is_active=True, token_version=0)
    db.add(user)
    db.commit()
    db.refresh(user)

    token = create_access_token(subject=user.id, token_version=user.token_version)

    # Deactivate the user
    user.is_active = False
    db.commit()

    # Try to access /auth/me with the token
    r = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401, f"Deactivated user should get 401, got {r.status_code}"


# ============================================================
# G4.4: CSRF
# ============================================================

def test_g44_csrf_blocks_cookie_auth_without_header(client, db):
    """G4.4: POST with cookie auth but no X-CSRF-Token → 403."""
    email = f"csrf_{int(time.time())}@test.com"
    user = User(email=email, hashed_password="x", token_version=0)
    db.add(user)
    db.commit()
    db.refresh(user)

    token = create_access_token(subject=user.id, token_version=user.token_version)

    # Make a POST with cookie auth but no CSRF header
    # Use a harmless endpoint that accepts POST (e.g., /api/events/batch)
    r = client.post(
        "/api/events/batch",
        json={"events": [{"session_id": "csrf_test", "event_type": "page_view", "payload_json": {}}]},
        cookies={"access_token": token},
    )
    # Should be 403 due to CSRF middleware (no X-CSRF-Token header)
    assert r.status_code == 403, f"Expected 403 CSRF failure, got {r.status_code}"


def test_g44_csrf_exempt_bearer_auth(client):
    """G4.4: POST with Bearer auth (no cookie) is exempt from CSRF check."""
    # This should NOT fail with 403 CSRF — it may fail with 401/422 but not 403 CSRF
    r = client.post(
        "/api/events/batch",
        json={"events": [{"session_id": "bearer_test", "event_type": "page_view", "payload_json": {}}]},
        headers={"Authorization": "Bearer fake_token_not_real"},
    )
    # The request should NOT be blocked by CSRF (may be 202 or 401, but not 403 CSRF)
    assert r.status_code != 403 or "CSRF" not in r.text


# ============================================================
# G4.5: Digest dedupe
# ============================================================

def test_g45_digest_fingerprint_deterministic():
    """G4.5: Same content → same fingerprint regardless of product order."""
    fp_a = compute_digest_fingerprint(1, [3, 1, 2], "hello narrative")
    fp_b = compute_digest_fingerprint(1, [1, 2, 3], "hello narrative")
    assert fp_a == fp_b, "Fingerprint should be order-insensitive"

    fp_c = compute_digest_fingerprint(1, [1, 2, 3], "different narrative")
    assert fp_a != fp_c, "Different narrative → different fingerprint"


def test_g45_digest_dedupe_skips_identical():
    """G4.5: After recording a digest, identical content is detected as duplicate."""
    user_id = 99999  # fake user id for isolation
    products = [1, 2, 3]
    narrative = "Test digest narrative"

    # First time — not a duplicate
    assert is_duplicate_digest(user_id, products, narrative) is False

    # Record it
    fp = compute_digest_fingerprint(user_id, products, narrative)
    record_digest_sent(user_id, fp)

    # Second time — should be detected as duplicate
    assert is_duplicate_digest(user_id, products, narrative) is True

    # Different content — not a duplicate
    assert is_duplicate_digest(user_id, products, "changed narrative") is False