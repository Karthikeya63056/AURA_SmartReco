import os
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application Settings loaded from environment variables or .env file."""

    MESH_API_KEY: str = "rsk_demo_key"
    MESH_BASE_URL: str = "https://api.meshapi.ai/v1"
    
    # Models configuration - default to free Mesh API models
    DEFAULT_CHAT_MODEL: str = "tencent/hy3"
    MAIN_CHAT_MODEL: str = "tencent/hy3"
    DEFAULT_EMBEDDING_MODEL: str = "sentence-transformers/all-minilm-l6-v2"

    DATABASE_URL: str = "sqlite:///./smartreco.db"
    CHROMA_PERSIST_DIR: str = "./chroma_data"
    JWT_SECRET: str = "your_jwt_secret_change_me_super_secret_key_12345"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 1440

    LANGCHAIN_TRACING_V2: bool = False
    LANGCHAIN_API_KEY: str = ""
    LANGCHAIN_PROJECT: str = "smartreco"

    SMTP_HOST: str = ""
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASS: str = ""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )


settings = Settings()
