import os
import sys
import logging
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables or .env file."""

    MESH_API_KEY: str = "rsk_demo_key"
    MESH_BASE_URL: str = "https://api.meshapi.ai/v1"

    # Models — defaults target free Mesh API models
    DEFAULT_CHAT_MODEL: str = "tencent/hy3"
    MAIN_CHAT_MODEL: str = "tencent/hy3"
    DEFAULT_EMBEDDING_MODEL: str = "sentence-transformers/all-minilm-l6-v2"

    DATABASE_URL: str = "sqlite:///./smartreco.db"
    CHROMA_PERSIST_DIR: str = "./chroma_data"

    # Auth — no weak public default; validated after load
    JWT_SECRET: str = ""
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 1440

    # True for local HTTP (cookie Secure=False). Set DEBUG=false in production HTTPS.
    DEBUG: bool = True

    LANGCHAIN_TRACING_V2: bool = False
    LANGCHAIN_API_KEY: str = ""
    LANGCHAIN_PROJECT: str = "smartreco"

    # SMTP Email Configuration
    SMTP_HOST: str = "smtp.gmail.com"
    SMTP_PORT: int = 465
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""  # Changed from SMTP_PASS to match .env
    MAIL_FROM: str = "no-reply@aura.smartreco.ai"

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
_validate_jwt_secret(settings.JWT_SECRET)