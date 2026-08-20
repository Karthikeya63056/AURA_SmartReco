"""
Synchronous Mesh API choke point.
All LLM and embedding calls go through this single module.
This is the ONLY place that imports from openai.
C2/C4 compliant: sync client only, no AsyncOpenAI.
"""
import logging
import time
from typing import Optional, List, Dict, Any
from openai import OpenAI
from app.config import settings

logger = logging.getLogger(__name__)

# After an HTTP 402, pause Mesh calls for this long before retrying.
# (Time-based instead of a permanent latch: the API key may be topped up
#  without restarting the app.)
MESH_RETRY_AFTER_SECONDS = 5 * 60


class MeshAPIUnavailable(RuntimeError):
    """Raised when Mesh API cannot be used and fallback is required."""


# Module-level sync client (C4: sync is allowed, async is forbidden)
_client: Optional[OpenAI] = None
_mesh_blocked_until: float = 0.0
_mesh_402_logged: bool = False


def get_client() -> OpenAI:
    """Get or create the module-level sync Mesh client."""
    global _client
    if _client is None:
        _client = OpenAI(
            api_key=settings.MESH_API_KEY,
            base_url=settings.MESH_BASE_URL,
            timeout=60.0,
            max_retries=2,
        )
    return _client


def chat(
    messages: List[Dict[str, str]],
    model: Optional[str] = None,
    temperature: float = 0.7,
    max_tokens: Optional[int] = None,
    response_format: Optional[Dict[str, Any]] = None,
) -> str:
    """
    Synchronous chat completion.
    
    Returns:
        Content string from the LLM response.
    
    Raises:
        MeshAPIUnavailable: If Mesh is blocked (402 latch active).
        Exception: Any other API error.
    """
    global _mesh_blocked_until, _mesh_402_logged
    
    if _mesh_blocked_until and time.time() < _mesh_blocked_until:
        raise MeshAPIUnavailable(
            "Mesh API is temporarily unavailable (HTTP 402); "
            f"retry window active until {time.ctime(_mesh_blocked_until)}"
        )
    
    client = get_client()
    selected_model = model or settings.DEFAULT_CHAT_MODEL
    
    kwargs: Dict[str, Any] = {
        "model": selected_model,
        "messages": messages,
        "temperature": temperature,
    }
    if max_tokens:
        kwargs["max_tokens"] = max_tokens
    if response_format:
        kwargs["response_format"] = response_format
    
    start = time.perf_counter()
    try:
        response = client.chat.completions.create(**kwargs)
        latency_ms = int((time.perf_counter() - start) * 1000)
        usage = response.usage
        logger.info(
            f"[Mesh] chat {selected_model}: {latency_ms}ms, "
            f"tokens={usage.total_tokens if usage else '?'}"
        )
        return response.choices[0].message.content or ""
    except Exception as e:
        error_str = str(e)
        if "402" in error_str:
            if not _mesh_402_logged:
                logger.warning(
                    f"Mesh API returned 402; blocking calls for "
                    f"{MESH_RETRY_AFTER_SECONDS // 60} minutes"
                )
                _mesh_402_logged = True
            _mesh_blocked_until = time.time() + MESH_RETRY_AFTER_SECONDS
            raise MeshAPIUnavailable(
                "Mesh API returned HTTP 402 (budget/quota exceeded)"
            ) from e
        logger.error(f"[Mesh] chat failed ({selected_model}): {e}")
        raise


def embed(text: str, model: Optional[str] = None) -> List[float]:
    """
    Synchronous embedding.
    
    Args:
        text: Single text string to embed.
        model: Model identifier (defaults to settings.DEFAULT_EMBEDDING_MODEL).
    
    Returns:
        List of embedding vector floats.
    
    Raises:
        MeshAPIUnavailable: If Mesh is blocked (402 latch active).
        Exception: Any other API error.
    """
    global _mesh_blocked_until, _mesh_402_logged
    
    if _mesh_blocked_until and time.time() < _mesh_blocked_until:
        raise MeshAPIUnavailable(
            "Mesh API is temporarily unavailable (HTTP 402)"
        )
    
    client = get_client()
    selected_model = model or settings.DEFAULT_EMBEDDING_MODEL
    
    try:
        response = client.embeddings.create(
            model=selected_model,
            input=[text],
        )
        return response.data[0].embedding
    except Exception as e:
        error_str = str(e)
        if "402" in error_str:
            if not _mesh_402_logged:
                logger.warning(
                    f"Mesh embeddings returned 402; blocking calls for "
                    f"{MESH_RETRY_AFTER_SECONDS // 60} minutes"
                )
                _mesh_402_logged = True
            _mesh_blocked_until = time.time() + MESH_RETRY_AFTER_SECONDS
            raise MeshAPIUnavailable(
                "Mesh API returned HTTP 402 (budget/quota exceeded)"
            ) from e
        logger.error(f"[Mesh] embed failed ({selected_model}): {e}")
        raise


def embed_batch(texts: List[str], model: Optional[str] = None) -> List[List[float]]:
    """
    Batch embedding for multiple texts.
    
    Args:
        texts: List of text strings to embed.
        model: Model identifier (defaults to settings.DEFAULT_EMBEDDING_MODEL).
    
    Returns:
        List of embedding vectors (one per input text).
    """
    if not texts:
        return []
    
    client = get_client()
    selected_model = model or settings.DEFAULT_EMBEDDING_MODEL
    
    try:
        response = client.embeddings.create(
            model=selected_model,
            input=texts,
        )
        return [item.embedding for item in response.data]
    except Exception as e:
        error_str = str(e)
        if "402" in error_str:
            global _mesh_blocked_until, _mesh_402_logged
            if not _mesh_402_logged:
                logger.warning(
                    f"Mesh batch embeddings returned 402; blocking calls for "
                    f"{MESH_RETRY_AFTER_SECONDS // 60} minutes"
                )
                _mesh_402_logged = True
            _mesh_blocked_until = time.time() + MESH_RETRY_AFTER_SECONDS
            raise MeshAPIUnavailable(
                "Mesh API returned HTTP 402 (budget/quota exceeded)"
            ) from e
        logger.error(f"[Mesh] embed_batch failed ({selected_model}): {e}")
        raise