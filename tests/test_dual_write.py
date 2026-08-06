import pytest
from app.models.product import Product
from app.services.product_service import (
    create_product,
    update_product,
    delete_product,
    get_product
)


def test_product_dual_write_create(db_session):
    """Test creating product inserts into SQLite DB."""
    product_data = {
        "title": "Test LangGraph Course",
        "category": "AI & Agents",
        "level": "Advanced",
        "price": 99.99,
        "rating": 4.9,
        "description": "Test description for LangGraph course dual-write test.",
        "tags": ["langgraph", "test", "ai"]
    }

    product = create_product(db_session, product_data)
    assert product.id is not None
    assert product.title == "Test LangGraph Course"

    # Verify query from SQL
    fetched = get_product(db_session, product.id)
    assert fetched is not None
    assert fetched.category == "AI & Agents"


def test_product_dual_write_update(db_session):
    """Test updating product updates SQLite DB."""
    product_data = {
        "title": "Initial Title",
        "category": "Web Dev",
        "level": "Beginner",
        "price": 49.99,
        "description": "Initial description.",
        "tags": ["web"]
    }

    product = create_product(db_session, product_data)
    updated = update_product(db_session, product.id, {"title": "Updated Title", "price": 59.99})

    assert updated is not None
    assert updated.title == "Updated Title"
    assert updated.price == 59.99


def test_product_dual_write_delete(db_session):
    """Test deleting product removes from SQLite DB."""
    product_data = {
        "title": "To Be Deleted Course",
        "category": "Testing",
        "level": "Beginner",
        "price": 10.00,
        "description": "Delete test.",
        "tags": ["delete"]
    }

    product = create_product(db_session, product_data)
    product_id = product.id

    success = delete_product(db_session, product_id)
    assert success is True

    fetched = get_product(db_session, product_id)
    assert fetched is None
