"""
Local embedding function using sentence-transformers (MiniLM-L6-v2).
Replaces OpenRouter API calls with fast local inference.
"""
import logging
from typing import List, Optional
from functools import lru_cache

from chromadb import EmbeddingFunction, Documents, Embeddings
from sentence_transformers import SentenceTransformer
from app.config import settings

logger = logging.getLogger(__name__)


class MeshEmbeddingUnavailable(RuntimeError):
    """Raised when embeddings cannot be generated."""


class MeshEmbeddingFunction(EmbeddingFunction[Documents]):
    """
    Local embedding function using MiniLM-L6-v2 (384-dim vectors).
    Drop-in replacement for OpenRouter API version.
    """

    _model: Optional[SentenceTransformer] = None
    _model_lock = None

    def __init__(self, model_name: str = None):
        # model_name parameter kept for API compatibility, but we use MiniLM-L6-v2
        self.model_name = "sentence-transformers/all-MiniLM-L6-v2"
        self._init_model()

    def _init_model(self):
        """Lazy-load the model on first use."""
        if self._model is None:
            try:
                logger.info(f"Loading embedding model: {self.model_name}")
                self._model = SentenceTransformer(self.model_name)
                logger.info(f"Embedding model loaded: dim={self._model.get_sentence_embedding_dimension()}")
            except Exception as e:
                logger.error(f"Failed to load embedding model: {e}")
                raise MeshEmbeddingUnavailable(f"Model load error: {e}")

    @lru_cache(maxsize=1024)
    def _embed_single(self, text: str) -> List[float]:
        """Embed a single text with caching."""
        try:
            # Truncate to ~500 tokens (MiniLM has 512 token limit)
            # Approximate: 1 token ≈ 4 chars, so 500 tokens ≈ 2000 chars
            truncated = text[:2000] if len(text) > 2000 else text
            embedding = self._model.encode(truncated, normalize_embeddings=True)
            return embedding.tolist()
        except Exception as e:
            logger.error(f"Embedding failed: {e}")
            raise MeshEmbeddingUnavailable(f"Embedding error: {e}")

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
            embeddings = self._model.encode(
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