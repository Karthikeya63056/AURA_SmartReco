import logging
from typing import List, Optional, Dict, Any
from sqlalchemy import or_
from sqlalchemy.orm import Session
from app.models.product import Product
from app.core.vector_store import get_products_collection
from app.core.embeddings import MeshEmbeddingUnavailable

logger = logging.getLogger(__name__)


def _build_product_document_text(product: Product) -> str:
    """Build string representation of product for embedding."""
    tags_str = ", ".join(product.tags) if isinstance(product.tags, list) else str(product.tags or "")
    return f"Title: {product.title}. Category: {product.category}. Level: {product.level}. Description: {product.description}. Tags: {tags_str}"


def _build_product_chroma_metadata(product: Product) -> Dict[str, Any]:
    """Build ChromaDB metadata dict for product filtering."""
    return {
        "product_id": product.id,
        "title": product.title,
        "category": product.category,
        "level": product.level,
        "price": float(product.price),
        "rating": float(product.rating),
        "is_popular": bool(product.is_popular),
        "is_trending": bool(product.is_trending)
    }


def create_product(db: Session, product_data: Dict[str, Any]) -> Product:
    """
    Dual-write product creation:
    1. SQL insert & flush to assign ID
    2. Upsert to Chroma vector store via MeshEmbeddingFunction
    3. SQL commit
    Fallback: On vector store error, rollback or flag needs_reindex=True.
    """
    product = Product(**product_data)
    try:
        db.add(product)
        db.flush()  # Assign ID

        # Chroma upsert
        collection = get_products_collection()
        doc_text = _build_product_document_text(product)
        metadata = _build_product_chroma_metadata(product)

        collection.upsert(
            ids=[str(product.id)],
            documents=[doc_text],
            metadatas=[metadata]
        )

        db.commit()
        db.refresh(product)
        return product
    except Exception as e:
        logger.error(f"ChromaDB dual-write failed during create_product for '{product.title}': {str(e)}")
        db.rollback()
        # Save to DB with needs_reindex=True
        product.needs_reindex = True
        db.add(product)
        db.commit()
        db.refresh(product)
        return product


def update_product(db: Session, product_id: int, product_data: Dict[str, Any]) -> Optional[Product]:
    """
    Dual-write product update:
    1. SQL update
    2. Re-generate embedding & Chroma upsert
    3. SQL commit
    """
    product = db.query(Product).filter(Product.id == product_id).first()
    if not product:
        return None

    for key, value in product_data.items():
        if hasattr(product, key):
            setattr(product, key, value)

    try:
        # Chroma upsert
        collection = get_products_collection()
        doc_text = _build_product_document_text(product)
        metadata = _build_product_chroma_metadata(product)

        collection.upsert(
            ids=[str(product.id)],
            documents=[doc_text],
            metadatas=[metadata]
        )
        product.needs_reindex = False
        db.commit()
        db.refresh(product)
        return product
    except Exception as e:
        product.needs_reindex = True
        db.commit()
        db.refresh(product)
        return product


def reindex_pending_products(db: Session) -> int:
    """
    Attempt to re-embed and upsert all products flagged with needs_reindex=True directly to ChromaDB.
    """
    pending = db.query(Product).filter(Product.needs_reindex == True).all()
    if not pending:
        return 0

    collection = get_products_collection()
    success_count = 0
    successful_batch: List[Product] = []
    for product in pending:
        try:
            doc_text = _build_product_document_text(product)
            metadata = _build_product_chroma_metadata(product)
            collection.upsert(
                ids=[str(product.id)],
                documents=[doc_text],
                metadatas=[metadata]
            )
            product.needs_reindex = False
            successful_batch.append(product)
            if len(successful_batch) >= 10:
                db.commit()
                success_count += len(successful_batch)
                successful_batch = []
        except Exception as e:
            logger.error(f"Reindex failed for product ID {product.id}: {str(e)}")

    if successful_batch:
        db.commit()
        success_count += len(successful_batch)

    return success_count


def delete_product(db: Session, product_id: int) -> bool:
    """
    Dual-write product deletion:
    1. SQL delete
    2. Chroma delete
    """
    product = db.query(Product).filter(Product.id == product_id).first()
    if not product:
        return False

    db.delete(product)

    try:
        collection = get_products_collection()
        collection.delete(ids=[str(product_id)])
    except Exception as e:
        logger.error(f"ChromaDB deletion failed for product ID {product_id}: {str(e)}")

    db.commit()
    return True


def get_product(db: Session, product_id: int) -> Optional[Product]:
    """Fetch product by ID."""
    return db.query(Product).filter(Product.id == product_id).first()


def reindex_needs_reindex_products() -> int:
    """
    Re-attempt Chroma dual-write for any products flagged needs_reindex=True.

    Called on app startup so transient Mesh/Chroma failures during create/update
    are recovered automatically without a manual reindex script run.
    Returns the number of products successfully reindexed.
    """
    from app.core.database import SessionLocal

    db = SessionLocal()
    try:
        pending_count = db.query(Product).filter(Product.needs_reindex == True).count()  # noqa: E712
        if not pending_count:
            logger.info("Startup reindex: no products with needs_reindex=True")
            return 0

        logger.info(f"Startup reindex: attempting to reindex {pending_count} product(s)...")
        success_count = reindex_pending_products(db)
        logger.info(
            f"Startup reindex complete: {success_count}/{pending_count} products recovered"
        )
        return success_count
    finally:
        db.close()


def list_products(
    db: Session,
    category: Optional[str] = None,
    level: Optional[str] = None,
    is_popular: Optional[bool] = None,
    is_trending: Optional[bool] = None,
    limit: int = 50,
    skip: int = 0
) -> List[Product]:
    """List products with optional filtering."""
    query = db.query(Product)
    if category:
        query = query.filter(Product.category == category)
    if level:
        query = query.filter(Product.level == level)
    if is_popular is not None:
        query = query.filter(Product.is_popular == is_popular)
    if is_trending is not None:
        query = query.filter(Product.is_trending == is_trending)
    return query.offset(skip).limit(limit).all()


def search_products_vector(
    query_text: str,
    n_results: int = 15,
    where_filter: Optional[Dict[str, Any]] = None
) -> List[Dict[str, Any]]:
    """
    Search for relevant candidate products.
    
    Primary: ChromaDB vector search using Mesh API query embedding.
    Fallback: SQL keyword search (when embedding API is unavailable).
    """
    # Try vector search first
    try:
        collection = get_products_collection()
        kwargs: Dict[str, Any] = {
            "query_texts": [query_text],
            "n_results": n_results
        }
        if where_filter:
            kwargs["where"] = where_filter

        results = collection.query(**kwargs)
        
        candidates = []
        if results and results.get("ids") and len(results["ids"]) > 0:
            ids = results["ids"][0]
            metadatas = results["metadatas"][0] if results.get("metadatas") else []
            documents = results["documents"][0] if results.get("documents") else []
            distances = results["distances"][0] if results.get("distances") else []

            for i in range(len(ids)):
                candidates.append({
                    "id": int(ids[i]),
                    "metadata": metadatas[i] if i < len(metadatas) else {},
                    "document": documents[i] if i < len(documents) else "",
                    "distance": distances[i] if i < len(distances) else 0.0
                })

        if candidates:
            return candidates
        # If no results from vector search, fall through to SQL fallback
        logger.info("Vector search returned 0 results, falling back to SQL keyword search.")
    except MeshEmbeddingUnavailable:
        logger.debug("Vector search skipped because Mesh embeddings are unavailable.")
    except Exception as e:
        logger.warning(f"Vector search failed ({str(e)}), falling back to SQL keyword search.")

    # SQL keyword fallback
    return _sql_keyword_search(query_text, n_results)


def _sql_keyword_search(query_text: str, n_results: int = 15) -> List[Dict[str, Any]]:
    """
    Fallback keyword search using SQL LIKE queries against product title,
    description, category, and tags. Extracts keywords from the query and
    scores products by number of keyword matches.
    """
    from app.core.database import SessionLocal

    # Extract meaningful keywords (3+ chars, lowercase)
    stop_words = {"the", "and", "for", "with", "from", "that", "this", "are", "was", "has", "have", "been"}
    keywords = [
        w.lower() for w in query_text.split()
        if len(w) >= 3 and w.lower() not in stop_words
    ]

    if not keywords:
        keywords = ["ai", "machine", "learning"]

    # JSON tags are not consistently searchable across supported SQL backends,
    # so use indexed text fields for the database-side candidate filter.
    predicates = []
    for keyword in keywords:
        pattern = f"%{keyword}%"
        predicates.extend((
            Product.title.ilike(pattern),
            Product.description.ilike(pattern),
            Product.category.ilike(pattern),
        ))

    candidate_limit = min(max(n_results, 1) * 3, 50)
    db = SessionLocal()
    try:
        products = (
            db.query(Product)
            .filter(or_(*predicates))
            .order_by(Product.rating.desc(), Product.is_popular.desc(), Product.is_trending.desc())
            .limit(candidate_limit)
            .all()
        )
        scored: List[tuple] = []

        for p in products:
            searchable = f"{p.title} {p.description} {p.category} {' '.join(p.tags) if isinstance(p.tags, list) else ''}".lower()
            score = sum(1 for kw in keywords if kw in searchable)
            if score > 0:
                scored.append((p, score))

        # Sort by match score descending
        scored.sort(key=lambda x: x[1], reverse=True)

        candidates = []
        for p, score in scored[:max(n_results, 0)]:
            doc_text = _build_product_document_text(p)
            metadata = _build_product_chroma_metadata(p)
            candidates.append({
                "id": p.id,
                "metadata": metadata,
                "document": doc_text,
                "distance": 1.0 - (score / max(len(keywords), 1))  # Approximate distance
            })

        logger.info(f"SQL keyword fallback returned {len(candidates)} candidates for query: '{query_text}'")
        return candidates
    finally:
        db.close()
