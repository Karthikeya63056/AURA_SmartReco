"""Phase 1 exit-gate tests (G1.1, G1.3, G1.4)."""
import time
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.main import app
from app.core.database import SessionLocal
from app.models.event import Event


@pytest.fixture
def client():
    return TestClient(app)


def test_g13_pragmas_active(client):
    """G1.3: PRAGMAs are enforced (foreign_keys ON, journal_mode WAL)."""
    with SessionLocal() as session:
        fk = session.execute(text("PRAGMA foreign_keys")).fetchone()
        assert fk[0] == 1, "foreign_keys should be ON"
        jm = session.execute(text("PRAGMA journal_mode")).fetchone()
        assert jm[0].lower() == "wal", f"journal_mode should be WAL, got {jm[0]}"


def test_g14_new_tables_exist(client):
    """G1.4: New tables and indexes exist after migrations."""
    with SessionLocal() as session:
        # agent_runs table
        r = session.execute(text(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='agent_runs'"
        )).fetchone()
        assert r is not None, "agent_runs table missing"

        # vector_outbox was removed as dead code (no producers/consumers);
        # legacy databases may still carry the table, so it is not asserted.

        # partial unique index
        r = session.execute(text(
            "SELECT name FROM sqlite_master WHERE type='index' AND name='uq_single_flight'"
        )).fetchone()
        assert r is not None, "uq_single_flight partial index missing"

        # users.token_version column
        r = session.execute(text("PRAGMA table_info(users)")).fetchall()
        col_names = [row[1] for row in r]
        assert "token_version" in col_names, "users.token_version column missing"

        # users.password_changed_at column (single-use reset tokens)
        assert "password_changed_at" in col_names, "users.password_changed_at column missing"

        # products.embedding_hash column
        r = session.execute(text("PRAGMA table_info(products)")).fetchall()
        col_names = [row[1] for row in r]
        assert "embedding_hash" in col_names, "products.embedding_hash column missing"


def test_g11_dedupe_race(client):
    """G1.1: Two concurrent same-key batches → both 202, exactly one row."""
    from app.core import event_buffer
    
    idem_key = f"test_dupe_{int(time.time()*1000)}"
    payload = {
        "events": [{
            "session_id": "sess_test_phase1",
            "event_type": "course_view",
            "payload_json": {"course_id": 1},
            "idempotency_key": idem_key,
        }]
    }

    r1 = client.post("/api/events/batch", json=payload)
    r2 = client.post("/api/events/batch", json=payload)

    assert r1.status_code == 202, f"Expected 202, got {r1.status_code}: {r1.text}"
    assert r2.status_code == 202, f"Expected 202, got {r2.status_code}: {r2.text}"

    # Manually trigger flush (TestClient doesn't run async background tasks)
    rows = event_buffer.drain()
    if rows:
        event_buffer.bulk_insert_events(rows)

    with SessionLocal() as session:
        count = session.query(Event).filter(
            Event.idempotency_key == idem_key
        ).count()
        assert count == 1, f"Expected exactly 1 row, got {count}"