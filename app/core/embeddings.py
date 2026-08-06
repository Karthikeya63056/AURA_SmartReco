import logging
from typing import List, Union
from chromadb import EmbeddingFunction, Documents, Embeddings
from app.core.llm import get_llm_client
from app.config import settings

logger = logging.getLogger(__name__)


class MeshEmbeddingFunction(EmbeddingFunction[Documents]):
    """
    Custom ChromaDB EmbeddingFunction that routes ALL embedding requests 
    (indexing and queries) through the Mesh API using 'sentence-transformers/all-minilm-l6-v2'.
    """

    def __init__(self, model_name: str = None):
        self.model_name = model_name or settings.DEFAULT_EMBEDDING_MODEL
        self.client = get_llm_client()

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
        
        try:
            response = self.client.embeddings.create(
                model=self.model_name,
                input=texts
            )
            embeddings = [item.embedding for item in response.data]
            return embeddings
        except Exception as e:
            logger.error(f"Error creating embeddings via Mesh API ({self.model_name}): {str(e)}")
            raise e
