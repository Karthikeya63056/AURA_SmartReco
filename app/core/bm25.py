"""
In-process Okapi BM25 keyword search over the product catalog.
Sparse half of hybrid retrieval — catches exact keyword matches that
vector similarity might miss.

Parameters: k1=1.5 (term frequency saturation), b=0.75 (length normalization).
Standard BM25 values used across information retrieval systems.
"""
import logging
import re
import threading
import time
from typing import List, Tuple, Optional

from rank_bm25 import BM25Okapi

from app.core.database import SessionLocal
from app.models.product import Product

logger = logging.getLogger(__name__)

# BM25 parameters (standard information retrieval defaults)
BM25_K1 = 1.5
BM25_B = 0.75

# Module-level singleton
_bm25_index: Optional[BM25Okapi] = None
_product_ids: List[int] = []
_index_lock = threading.Lock()
_index_built_at: Optional[float] = None


def _tokenize(text: str) -> List[str]:
    """
    Simple tokenizer: lowercase, split on non-alphanumeric, drop short tokens.
    Good enough for course catalog; not a production search engine.
    """
    if not text:
        return []
    # Lowercase + split on non-word characters
    tokens = re.findall(r'\w+', text.lower())
    # Drop tokens shorter than 2 chars (noise)
    return [t for t in tokens if len(t) >= 2]


def build_index(force: bool = False) -> int:
    """
    Build BM25 index from all products in the catalog.
    
    Args:
        force: Rebuild even if index already exists
    
    Returns:
        Number of products indexed
    """
    global _bm25_index, _product_ids, _index_built_at
    
    with _index_lock:
        if _bm25_index is not None and not force:
            return len(_product_ids)
        
        with SessionLocal() as session:
            # Index the full catalog (Product has no is_active column)
            products = session.query(Product).all()
            
            if not products:
                logger.warning("[BM25] No products to index")
                _bm25_index = None
                _product_ids = []
                return 0
            
            # Build corpus: title + category + level + description + tags
            corpus = []
            ids = []
            for p in products:
                # Concatenate searchable text fields
                text_parts = [
                    p.title or "",
                    p.category or "",
                    p.level or "",
                    p.description or "",
                ]
                # Add tags if present
                if p.tags and isinstance(p.tags, list):
                    text_parts.extend(p.tags)
                # Add skills if present
                if p.skills_taught and isinstance(p.skills_taught, list):
                    text_parts.extend(p.skills_taught)
                
                full_text = " ".join(text_parts)
                tokens = _tokenize(full_text)
                
                if tokens:  # Skip products with no tokenizable content
                    corpus.append(tokens)
                    ids.append(p.id)
            
            if not corpus:
                logger.warning("[BM25] No tokenizable content found")
                _bm25_index = None
                _product_ids = []
                return 0
            
            _bm25_index = BM25Okapi(corpus, k1=BM25_K1, b=BM25_B)
            _product_ids = ids
            _index_built_at = time.time()
            
            logger.info(f"[BM25] Index built: {len(ids)} products")
            return len(ids)


def search(query: str, k: int = 20) -> List[Tuple[int, float]]:
    """
    Search the catalog using BM25 keyword matching.
    
    Args:
        query: Search query string
        k: Maximum number of results to return
    
    Returns:
        List of (product_id, score) tuples, sorted by score descending
    """
    global _bm25_index
    
    # Lazy-build index on first search
    if _bm25_index is None:
        build_index()
    
    if _bm25_index is None:
        return []
    
    # Tokenize query
    query_tokens = _tokenize(query)
    if not query_tokens:
        return []
    
    # Get BM25 scores for all documents
    scores = _bm25_index.get_scores(query_tokens)
    
    # Pair scores with product IDs and sort
    scored = list(zip(_product_ids, scores))
    scored.sort(key=lambda x: x[1], reverse=True)
    
    # Return top k (filter out zero scores)
    return [(pid, score) for pid, score in scored[:k] if score > 0]


def invalidate():
    """
    Force index rebuild on next search call.
    Call this after product catalog changes (create/update/delete).
    """
    global _bm25_index, _product_ids, _index_built_at
    with _index_lock:
        _bm25_index = None
        _product_ids = []
        _index_built_at = None
        logger.info("[BM25] Index invalidated; will rebuild on next search")