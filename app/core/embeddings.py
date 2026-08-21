"""
Local embedding function using sentence-transformers (MiniLM-L6-v2).
Replaces OpenRouter API calls with fast local inference.
"""
import logging
import threading
from typing import Dict, List, Optional

from chromadb import EmbeddingFunction, Documents, Embeddings
from sentence_transformers import SentenceTransformer

logger = logging.getLogger(__name__)

_MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"

# Module-level singleton: the model is expensive to load, so share one
# instance across all MeshEmbeddingFunction instances (and threads).
_MODEL: Optional[SentenceTransformer] = None
_MODEL_LOCK = threading.Lock()

# Bounded cache for single-text embeddings, keyed by raw text.
_SINGLE_CACHE_MAX_SIZE = 1024
_single_cache: Dict[str, List[float]] = {}
_single_cache_lock = threading.Lock()


class MeshEmbeddingUnavailable(RuntimeError):
    """Raised when embeddings cannot be generated."""


def _get_model() -> SentenceTransformer:
    """Return the shared SentenceTransformer, loading it exactly once."""
    global _MODEL
    if _MODEL is None:
        with _MODEL_LOCK:
            if _MODEL is None:
                try:
                    logger.info(f"Loading embedding model: {_MODEL_NAME}")
                    model = SentenceTransformer(_MODEL_NAME)
                except Exception as e:
                    logger.error(f"Failed to load embedding model: {e}")
                    raise MeshEmbeddingUnavailable(f"Model load error: {e}")
                dim = None
                for attr in ("get_embedding_dimension", "get_sentence_embedding_dimension"):
                    fn = getattr(model, attr, None)
                    if fn:
                        dim = fn()
                        break
                if dim is not None:
                    logger.info(f"Embedding model loaded: dim={dim}")
                _MODEL = model
    return _MODEL


class MeshEmbeddingFunction(EmbeddingFunction[Documents]):
    """
    Local embedding function using MiniLM-L6-v2 (384-dim vectors).
    Drop-in replacement for OpenRouter API version.
    """

    def __init__(self, model_name: str = None):
        # model_name parameter kept for API compatibility, but we use MiniLM-L6-v2
        self.model_name = _MODEL_NAME

    def _embed_single(self, text: str) -> List[float]:
        """Embed a single text with a bounded raw-text cache."""
        with _single_cache_lock:
            cached = _single_cache.get(text)
        if cached is not None:
            return list(cached)

        try:
            # Truncate to ~500 tokens (MiniLM has 512 token limit)
            # Approximate: 1 token ≈ 4 chars, so 500 tokens ≈ 2000 chars
            truncated = text[:2000] if len(text) > 2000 else text
            embedding = _get_model().encode(truncated, normalize_embeddings=True)
        except Exception as e:
            logger.error(f"Embedding failed: {e}")
            raise MeshEmbeddingUnavailable(f"Embedding error: {e}")

        result = embedding.tolist()
        with _single_cache_lock:
            if len(_single_cache) >= _SINGLE_CACHE_MAX_SIZE:
                # Evict the oldest entry (insertion order)
                _single_cache.pop(next(iter(_single_cache)))
            _single_cache[text] = result
        return result

    def __call__(self, input: Documents) -> Embeddings:
        """Embed a batch of documents."""
        if not input:
            return []

        # Ensure input is a list of strings
        if isinstance(input, str):
            texts = [input]
        else:
            texts = list(input)

        # Batch encode for better performance
        try:
            # Truncate all texts
            truncated_texts = [t[:2000] if len(t) > 2000 else t for t in texts]
            embeddings = _get_model().encode(
                truncated_texts,
                normalize_embeddings=True,
                batch_size=32,
                show_progress_bar=False
            )
            return [emb.tolist() for emb in embeddings]
        except Exception as e:
            logger.error(f"Batch embedding failed: {e}")
            # Fall back to single embeddings
            embeddings = []
            for text in texts:
                embeddings.append(self._embed_single(text))
            return embeddings
