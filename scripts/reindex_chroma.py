import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import logging
import app._grpc_fix
from app.core.database import SessionLocal
from app.services.product_service import reindex_pending_products

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def reindex():
    db = SessionLocal()
    try:
        logger.info("Attempting re-indexing of pending products into ChromaDB vector store...")
        success_count = reindex_pending_products(db)
        logger.info(f"Successfully dual-indexed {success_count} pending products into ChromaDB!")
    finally:
        db.close()


if __name__ == "__main__":
    reindex()
