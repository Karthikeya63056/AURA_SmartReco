"""
One-time script to rebuild ChromaDB 'products' collection with MiniLM-L6-v2 embeddings.
Deletes old collection and re-indexes all products from database.
"""
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

import logging
from app.core.database import SessionLocal
from app.models.product import Product
from app.core.vector_store import get_chroma_client, get_embedding_function
from app.services.product_service import (
    _build_product_document_text,
    _build_product_chroma_metadata
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


def reindex_all_products():
    """Delete old collection and rebuild with MiniLM embeddings."""
    
    logger.info("=" * 80)
    logger.info("Starting full reindex with MiniLM-L6-v2 (384-dim vectors)")
    logger.info("=" * 80)
    
    # Step 1: Get database session
    db = SessionLocal()
    
    try:
        # Step 2: Count products
        total_products = db.query(Product).count()
        logger.info(f"Found {total_products} products in database")
        
        if total_products == 0:
            logger.warning("No products found. Nothing to reindex.")
            return
        
        # Step 3: Delete old ChromaDB collection
        logger.info("Deleting old 'products' collection from ChromaDB...")
        client = get_chroma_client()
        try:
            client.delete_collection("products")
            logger.info("✓ Old collection deleted")
        except Exception as e:
            logger.info(f"Collection didn't exist or already deleted: {e}")
        
        # Step 4: Create new collection with MiniLM embedding function
        logger.info("Creating new 'products' collection with MiniLM-L6-v2...")
        embedding_fn = get_embedding_function()
        collection = client.get_or_create_collection(
            name="products",
            embedding_function=embedding_fn,
            metadata={"description": "SmartReco Course Catalog Vector Store"}
        )
        logger.info(f"✓ New collection created (dimension will be 384)")
        
        # Step 5: Fetch all products
        logger.info("Fetching all products from database...")
        products = db.query(Product).all()
        logger.info(f"✓ Loaded {len(products)} products into memory")
        
        # Step 6: Batch upsert to ChromaDB
        logger.info("Reindexing products (batch upsert to ChromaDB)...")
        batch_size = 50
        total_indexed = 0
        
        for i in range(0, len(products), batch_size):
            batch = products[i:i + batch_size]
            
            # Prepare batch data
            ids = []
            documents = []
            metadatas = []
            
            for product in batch:
                ids.append(str(product.id))
                documents.append(_build_product_document_text(product))
                metadatas.append(_build_product_chroma_metadata(product))
            
            # Upsert batch
            collection.upsert(
                ids=ids,
                documents=documents,
                metadatas=metadatas
            )
            
            total_indexed += len(batch)
            logger.info(f"  Indexed {total_indexed}/{len(products)} products")
        
        # Step 7: Clear all needs_reindex flags
        logger.info("Clearing needs_reindex flags...")
        db.query(Product).filter(Product.needs_reindex == True).update(
            {Product.needs_reindex: False},
            synchronize_session=False
        )
        db.commit()
        logger.info("✓ All needs_reindex flags cleared")
        
        # Step 8: Verify
        final_count = collection.count()
        logger.info("=" * 80)
        logger.info(f"✓ Reindex complete: {final_count} products indexed")
        logger.info(f"  Collection: products")
        logger.info(f"  Embedding model: sentence-transformers/all-MiniLM-L6-v2")
        logger.info(f"  Vector dimension: 384")
        logger.info(f"  ChromaDB path: ./chroma_data")
        logger.info("=" * 80)
        
    except Exception as e:
        logger.error(f"Reindex failed: {e}", exc_info=True)
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    reindex_all_products()