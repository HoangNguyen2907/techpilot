from pathlib import Path

from pydantic import computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(BACKEND_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Supabase API & Auth
    SUPABASE_URL: str
    SUPABASE_ANON_KEY: str
    SUPABASE_SERVICE_ROLE_KEY: str

    # Direct Postgres connection for Alembic & DB access
    DATABASE_URL: str
    DATABASE_PASSWORD: str | None = None

    # OpenAI configuration
    OPENAI_API_KEY: str
    OPENAI_CHAT_MODEL: str = "gpt-6-luna"
    OPENAI_CONTEXT_WINDOW_TOKENS: int = 32_768
    OPENAI_MAX_COMPLETION_TOKENS: int = 2_048
    OPENAI_MAX_HISTORY_MESSAGES: int = 100
    OPENAI_EMBEDDING_MODEL: str = "text-embedding-3-small"
    OPENAI_EMBEDDING_DIMENSIONS: int = 1536
    OPENAI_SUMMARY_MODEL: str = "gpt-4o-mini"
    OPENAI_SUMMARY_MAX_COMPLETION_TOKENS: int = 600

    # Server configuration
    ALLOWED_ORIGINS: str = "http://localhost:5173"

    @computed_field
    @property
    def allowed_origins(self) -> list[str]:
        return [
            origin.strip()
            for origin in self.ALLOWED_ORIGINS.split(",")
            if origin.strip()
        ]

    @computed_field
    @property
    def sync_database_url(self) -> str:
        """Returns a psycopg3-compatible connection string for SQLAlchemy/Alembic."""
        url = self.DATABASE_URL
        if url.startswith("postgresql://"):
            return url.replace("postgresql://", "postgresql+psycopg://", 1)
        return url


settings = Settings()  # type:ignore
