import logging
import time
from functools import lru_cache
from typing import List
from chromadb import EmbeddingFunction, Documents, Embeddings
from openai import OpenAI
from app.config import settings

logger = logging.getLogger(__name__)

# After an HTTP 402, pause embedding calls for this long before retrying.
# (Time-based instead of a permanent latch: the API key may be topped up
#  without restarting the app.)
EMBEDDINGS_RETRY_AFTER_SECONDS = 5 * 60


class MeshEmbeddingUnavailable(RuntimeError):
    """Raised when Mesh embeddings cannot be used and SQL fallback is required."""


class MeshEmbeddingFunction(EmbeddingFunction[Documents]):
    """
    Custom ChromaDB EmbeddingFunction that routes ALL embedding requests 
    (indexing and queries) through the Mesh API using 'sentence-transformers/all-minilm-l6-v2'.
    """

    def __init__(self, model_name: str = None):
        self.model_name = model_name or settings.DEFAULT_EMBEDDING_MODEL
        # Chroma's embedding interface is synchronous, so it needs its own
        # synchronous client rather than the async chat-completion client.
        self.client = OpenAI(
            base_url=settings.MESH_BASE_URL,
            api_key=settings.MESH_API_KEY,
        )
        self._embedding_402_logged = False
        self._embeddings_blocked_until = 0.0

    @lru_cache(maxsize=128)
    def _embed_text(self, text: str) -> tuple[float, ...]:
        response = self.client.embeddings.create(
            model=self.model_name,
            input=[text],
        )
        return tuple(response.data[0].embedding)

    def __call__(self, input: Documents) -> Embeddings:
        """
        Embed documents/texts using Mesh API.
        
        Args:
            input: List of strings (Documents)
            
        Returns:
            List of embedding vector floats
        """
        if not input:
            return []
            
        # Ensure input is a list of strings
        texts = list(input) if isinstance(input, (list, tuple)) else [str(input)]

        if self._embeddings_blocked_until and time.time() < self._embeddings_blocked_until:
            raise MeshEmbeddingUnavailable(
                "Mesh embeddings are temporarily unavailable (HTTP 402); "
                "SQL search fallback in use until retry window elapses"
            )

        try:
            return [list(self._embed_text(text)) for text in texts]
        except Exception as e:
            if "402" in str(e):
                if not self._embedding_402_logged:
                    logger.warning(
                        "Mesh embeddings are unavailable (HTTP 402); using SQL search "
                        f"fallback. Will retry in {EMBEDDINGS_RETRY_AFTER_SECONDS // 60} min."
                    )
                    self._embedding_402_logged = True
                self._embeddings_blocked_until = time.time() + EMBEDDINGS_RETRY_AFTER_SECONDS
                raise MeshEmbeddingUnavailable(
                    "Mesh embeddings are unavailable (HTTP 402)"
                ) from e
            else:
                logger.error(
                    f"Error creating embeddings via Mesh API ({self.model_name}): {str(e)}"
                )
            raise
