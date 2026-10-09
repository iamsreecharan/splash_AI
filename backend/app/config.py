from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SPLASH_", extra="ignore")

    env: str = "development"
    demo_mode: bool = Field(False, validation_alias=AliasChoices("SPLASH_DEMO_MODE", "SPLASH_DEMO_ENABLED"))
    database_url: str = "postgresql+psycopg://splash:splash@localhost:5432/splash"
    redis_url: str = "redis://localhost:6379/0"
    cors_origins: list[str] = ["http://localhost:5173"]
    public_api_url: str = "http://localhost:8000"
    web_url: str = "http://localhost:5173"
    supabase_url: str = ""
    supabase_anon_key: str = ""
    github_app_id: str = ""
    github_app_slug: str = ""
    github_private_key: str = ""
    github_private_key_path: str = ""
    github_client_id: str = ""
    github_client_secret: str = ""
    github_webhook_secret: str = ""
    token_encryption_key: str = ""
    ai_enabled: bool = False
    openai_api_key: str = ""
    openai_model: str = "gpt-4.1-mini"
    ai_max_input_tokens: int = 6000
    ai_max_output_tokens: int = 700
    ai_max_snippets: int = 8
    ai_max_snippet_chars: int = 1800
    ai_max_request_usd: float = 0.02
    ai_max_total_usd: float = 0.02
    celery_always_eager: bool = False
    max_repository_files: int = 1000
    max_repository_bytes: int = 8_000_000
    max_file_bytes: int = 150_000
    max_webhook_bytes: int = 1_000_000
    max_snapshots_per_repository: int = 10

    @model_validator(mode="after")
    def safe_deployment(self):
        if self.env not in {"development", "test", "production"}:
            raise ValueError("SPLASH_ENV must be development, test, or production")
        if self.env == "production":
            if self.demo_mode or self.celery_always_eager:
                raise ValueError("Demo identity and eager jobs are forbidden in production")
            if not self.supabase_url.startswith("https://"):
                raise ValueError("Production requires an HTTPS Supabase URL")
            if not self.public_api_url.startswith("https://") or not self.web_url.startswith("https://"):
                raise ValueError("Production API and web URLs must use HTTPS")
            if not self.database_url.startswith("postgresql"):
                raise ValueError("Production requires PostgreSQL")
            if "*" in self.cors_origins:
                raise ValueError("Production CORS must name allowed origins")
        if self.database_url.startswith("sqlite") and self.env != "test":
            raise ValueError("SQLite is restricted to backend unit tests")
        if self.celery_always_eager and self.env != "test":
            raise ValueError("Eager jobs are restricted to backend unit tests")
        return self

    @property
    def github_configured(self) -> bool:
        return bool(self.github_app_id and self.github_app_slug and
                    (self.github_private_key or self.github_private_key_path) and
                    self.github_client_id and self.github_client_secret and
                    self.github_webhook_secret and self.token_encryption_key)

    @property
    def auth_configured(self) -> bool:
        return bool(self.supabase_url and self.supabase_anon_key)

    def private_key(self) -> str:
        return Path(self.github_private_key_path).read_text() if self.github_private_key_path else self.github_private_key


@lru_cache
def get_settings() -> Settings:
    return Settings()
