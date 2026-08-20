import pytest


def test_get_products_endpoint(client):
    """Test public products endpoint."""
    response = client.get("/api/products")
    assert response.status_code == 200
    assert isinstance(response.json(), list)


def test_get_recommendations_endpoint(client):
    """Test recommendations endpoint returns cold-start or active recommendation."""
    response = client.get("/api/recommendations")
    assert response.status_code == 200
    data = response.json()
    assert "narrative" in data
    assert "product_ids" in data


def test_ingest_event_batch_endpoint(client):
    """Test event batch ingestion endpoint."""
    from app.core import event_buffer  # Rev5: manual flush
    
    payload = {
        "events": [
            {
                "session_id": "test_session_123",
                "event_type": "page_view",
                "payload_json": {"path": "/"},
                "idempotency_key": "key_test_1"
            }
        ]
    }
    response = client.post("/api/events/batch", json=payload)
    assert response.status_code == 202
    data = response.json()
    # Rev5: async buffer returns "queued" + "accepted"
    assert data["status"] == "queued"
    assert data["accepted"] == 1
    
    # Manually flush buffer (TestClient doesn't run async background tasks)
    rows = event_buffer.drain()
    if rows:
        event_buffer.bulk_insert_events(rows)