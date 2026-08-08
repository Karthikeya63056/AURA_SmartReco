import logging
from functools import lru_cache
from typing import List
from chromadb import EmbeddingFunction, Documents, Embeddings
from openai import OpenAI
from app.config import settings

logger = logging.getLogger(__name__)


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
        self._embeddings_disabled = False

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

        if self._embeddings_disabled:
            raise MeshEmbeddingUnavailable(
                "Mesh embeddings are disabled after an HTTP 402 response"
            )

        try:
            return [list(self._embed_text(text)) for text in texts]
        except Exception as e:
            if "402" in str(e):
                if not self._embedding_402_logged:
                    logger.warning(
                        "Mesh embeddings are unavailable (HTTP 402); using SQL search fallback."
                    )
                    self._embedding_402_logged = True
                self._embeddings_disabled = True
                raise MeshEmbeddingUnavailable(
                    "Mesh embeddings are unavailable (HTTP 402)"
                ) from e
            else:
                logger.error(
                    f"Error creating embeddings via Mesh API ({self.model_name}): {str(e)}"
                )
            raise
