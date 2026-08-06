import logging
import app._grpc_fix
from app.core.database import SessionLocal
from app.models.product import Product
from app.services.product_service import update_product

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def reindex():
    db = SessionLocal()
    try:
        products = db.query(Product).all()
        logger.info(f"Re-indexing {len(products)} products into ChromaDB via Mesh API embeddings...")
        success_count = 0
        for p in products:
            res = update_product(db, p.id, {})
            if res and not res.needs_reindex:
                success_count += 1
        logger.info(f"Successfully dual-indexed {success_count}/{len(products)} products into ChromaDB vector store!")
    finally:
        db.close()


if __name__ == "__main__":
    reindex()
