import logging
from typing import Any, Dict, List, Optional
from openai import OpenAI
from app.config import settings

logger = logging.getLogger(__name__)

# Initialize single Mesh API client instance using standard OpenAI SDK
_client = OpenAI(
    base_url=settings.MESH_BASE_URL,
    api_key=settings.MESH_API_KEY
)


def get_llm_client() -> OpenAI:
    """Return configured OpenAI client pointed to Mesh API base_url."""
    return _client


def generate_chat_completion(
    model: Optional[str] = None,
    messages: List[Dict[str, str]] = None,
    temperature: float = 0.7,
    max_tokens: Optional[int] = None,
    response_format: Optional[Dict[str, Any]] = None
) -> str:
    """
    Single source of truth for all chat completions via Mesh API.
    
    Args:
        model: Model identifier (defaults to settings.DEFAULT_CHAT_MODEL: 'tencent/hy3')
        messages: List of message dictionaries with 'role' and 'content'
        temperature: Sampling temperature
        max_tokens: Optional max output token limit
        response_format: Optional JSON schema enforcement
        
    Returns:
        Content string returned by LLM
    """
    selected_model = model or settings.DEFAULT_CHAT_MODEL
    messages = messages or []

    try:
        kwargs: Dict[str, Any] = {
            "model": selected_model,
            "messages": messages,
            "temperature": temperature
        }
        if max_tokens:
            kwargs["max_tokens"] = max_tokens
        if response_format:
            kwargs["response_format"] = response_format

        response = _client.chat.completions.create(**kwargs)
        return response.choices[0].message.content or ""
    except Exception as e:
        logger.error(f"Error in Mesh API chat completion call ({selected_model}): {str(e)}")
        raise e
