import os
os.environ["ANONYMIZED_TELEMETRY"] = "False"

import logging
import chromadb
from chromadb.api.models.Collection import Collection
from app.config import settings
from app.core.embeddings import MeshEmbeddingFunction

logger = logging.getLogger(__name__)

_client = None
_embedding_function = None


def get_chroma_client() -> chromadb.PersistentClient:
    """Initialize or return persistent ChromaDB client."""
    global _client
    if _client is None:
        os.makedirs(settings.CHROMA_PERSIST_DIR, exist_ok=True)
        _client = chromadb.PersistentClient(path=settings.CHROMA_PERSIST_DIR)
    return _client


def get_embedding_function() -> MeshEmbeddingFunction:
    """Return singleton instance of custom MeshEmbeddingFunction."""
    global _embedding_function
    if _embedding_function is None:
        _embedding_function = MeshEmbeddingFunction()
    return _embedding_function


def get_products_collection() -> Collection:
    """Get or create the 'products' Chroma collection configured with MeshEmbeddingFunction."""
    client = get_chroma_client()
    embedding_fn = get_embedding_function()
    
    collection = client.get_or_create_collection(
        name="products",
        embedding_function=embedding_fn,
        metadata={"description": "SmartReco Course Catalog Vector Store"}
    )
    return collection
