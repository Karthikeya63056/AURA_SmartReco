import os
import sys
import logging
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables or .env file."""

    # Mesh API (chat completions)
    MESH_API_KEY: str = ""
    MESH_BASE_URL: str = "https://api.meshapi.ai/v1"

    # Embeddings are local (sentence-transformers MiniLM, no API needed).
    # The OpenRouter fields below are legacy/unused and kept only for
    # backwards compatibility with existing .env files.
    EMBEDDINGS_BASE_URL: str = "https://openrouter.ai/api/v1"
    EMBEDDINGS_API_KEY: str = ""
    EMBEDDINGS_MODEL: str = "sentence-transformers/all-MiniLM-L6-v2"

    # Models — defaults target free Mesh API models
    DEFAULT_CHAT_MODEL: str = "tencent/hy3"
    MAIN_CHAT_MODEL: str = "tencent/hy3"
    DEFAULT_EMBEDDING_MODEL: str = "sentence-transformers/all-MiniLM-L6-v2"

    DATABASE_URL: str = "sqlite:///./smartreco.db"
    CHROMA_PERSIST_DIR: str = "./chroma_data"

    # Auth — no weak public default; validated after load
    JWT_SECRET: str = ""
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 1440

    # Login brute-force protection
    LOGIN_MAX_ATTEMPTS: int = 5
    LOGIN_WINDOW_MINUTES: int = 15

    # Recommendation agent tuning
    REC_MIN_EVENTS: int = 5
    REC_COOLDOWN_SECONDS: int = 90

    # Cache TTL for recommendation payloads
    CACHE_TTL_SECONDS: int = 900

    # LLM gateway
    LLM_ENABLED: bool = True
    LLM_DAILY_BUDGET_USD: float = 1.00
    LLM_COST_PER_1K_TOKENS: float = 0.002

    # Proxy/URL configuration
    TRUST_PROXY: bool = False
    BASE_URL: str = "http://localhost:8000"

    # Test mode: skip schedulers, sweeps, and run recovery on startup
    TESTING: bool = False

    # False for production HTTPS (cookie Secure=True). Set DEBUG=true only for local HTTP dev.
    DEBUG: bool = False

    LANGCHAIN_TRACING_V2: bool = False
    LANGCHAIN_API_KEY: str = ""
    LANGCHAIN_PROJECT: str = "smartreco"

    # SMTP Email Configuration
    SMTP_HOST: str = "smtp.gmail.com"
    SMTP_PORT: int = 465
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""  # matches .env.example; SMTP_SSL is used → 465
    MAIL_FROM: str = "no-reply@aura.smartreco.ai"

    # Comma-separated list of allowed CORS origins (cookie auth requires the
    # exact scheme://host:port that the frontend is served from)
    CORS_ORIGINS: str = "http://localhost:8000,http://127.0.0.1:8000"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


def _validate_jwt_secret(secret: str) -> None:
    """Refuse to start with a missing or placeholder JWT secret."""
    weak_prefixes = ("your_jwt", "change_me", "secret", "placeholder")
    if not secret or len(secret.strip()) < 32:
        print(
            "FATAL: JWT_SECRET must be set in .env to a strong value "
            "(at least 32 characters).",
            file=sys.stderr,
        )
        sys.exit(1)

    lowered = secret.strip().lower()
    example_value = "generate_a_long_random_string"
    if lowered == example_value or lowered.startswith(example_value):
        print(
            "FATAL: JWT_SECRET is still the .env.example placeholder value. "
            "Generate a real secret (e.g. `python -c \"import secrets; "
            "print(secrets.token_urlsafe(48))\"`) and set it in .env.",
            file=sys.stderr,
        )
        sys.exit(1)
    if any(lowered.startswith(p) or p in lowered for p in weak_prefixes):
        # Allow long random secrets that happen to contain words; block obvious placeholders
        if lowered.startswith("your_jwt") or lowered.startswith("change_me"):
            print(
                "FATAL: JWT_SECRET is still a placeholder. "
                "Set a strong random secret in .env.",
                file=sys.stderr,
            )
            sys.exit(1)


logger = logging.getLogger(__name__)

settings = Settings()
if not settings.MESH_API_KEY or settings.MESH_API_KEY.strip() == "rsk_demo_key":
    logger.warning(
        "MESH_API_KEY is missing or uses the demo placeholder; Mesh-backed features "
        "will fall back to SQL search where possible."
    )
if not settings.EMBEDDINGS_API_KEY:
    logger.warning(
        "EMBEDDINGS_API_KEY not set; vector search will be unavailable. "
        "Set it in .env to enable embeddings."
    )
_validate_jwt_secret(settings.JWT_SECRET)