"""Application configuration, loaded from environment variables (and `.env`)."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent


# The test suite sets FINSENSE_IGNORE_ENV_FILE so a developer's .env cannot change test behaviour.
_ENV_FILES = () if os.environ.get("FINSENSE_IGNORE_ENV_FILE") else (BACKEND_DIR.parent / ".env", BACKEND_DIR / ".env")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_ENV_FILES,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: Literal["development", "production", "test"] = "development"
    app_name: str = "FinSense AI"

    # Runtime connection uses the least-privilege role; migrations use the owner.
    database_url: SecretStr = SecretStr("postgresql+psycopg://finsense_app@localhost:5432/finsense")
    migration_database_url: SecretStr | None = None

    # Comma-separated in the environment, e.g. "http://localhost:5173,https://app.example.com".
    cors_origins: Annotated[list[str], NoDecode] = Field(default_factory=lambda: ["http://localhost:5173"])

    # Sessions are opaque random tokens stored hashed in the database.
    session_ttl_hours: int = 12
    cookie_secure: bool | None = None  # None -> secure unless development/test
    cookie_samesite: Literal["lax", "strict"] = "lax"
    trust_proxy_headers: bool = False

    upload_dir: Path = BACKEND_DIR / "var" / "uploads"
    model_dir: Path = BACKEND_DIR / "var" / "models"
    cache_dir: Path = BACKEND_DIR / "var" / "cache"
    max_upload_mb: int = 15
    max_csv_mb: int = 20
    max_pdf_pages: int = 300
    parser_timeout_s: int = 60

    # Embeddings: "wordllama" ships its weights inside the pip package and
    # works offline. "fastembed" (BAAI/bge-small-en-v1.5) downloads from
    # Hugging Face on first use and needs EMBEDDING_DIM=384 + a fresh migration.
    embedding_provider: Literal["wordllama", "fastembed"] = "wordllama"
    embedding_model: str = "l2_supercat"
    embedding_dim: int = 256

    reranker: Literal["none", "fastembed"] = "none"
    reranker_model: str = "Xenova/ms-marco-MiniLM-L-6-v2"

    chunk_target_words: int = 180
    chunk_overlap_words: int = 30

    retrieval_candidates: int = 30
    retrieval_top_k: int = 6
    rrf_k: int = 60
    rrf_vector_weight: float = 0.3
    rrf_lexical_weight: float = 1.0
    # Evidence gate (see retrieval.apply_evidence_gate and docs/RAG_EVALUATION.md).
    # A question is answerable when the best chunk covers enough of the
    # query's informative terms or is very similar to it; further chunks are
    # admitted relative to that best chunk.
    answer_min_coverage: float = 0.5
    answer_min_similarity: float = 0.6
    include_min_coverage: float = 0.3
    include_coverage_ratio: float = 0.6
    include_similarity_margin: float = 0.08
    context_word_budget: int = 1600
    max_chunks_per_document: int = 4

    llm_provider: Literal["none", "anthropic", "openai_compatible"] = "none"
    llm_model: str = ""
    llm_api_key: SecretStr | None = None
    llm_base_url: str = ""
    llm_timeout_s: float = 45.0
    llm_max_tokens: int = 900

    # Financial conventions (shown to users next to every metric).
    risk_free_rate: float = 0.065
    trading_days_per_year: int = 252
    stale_after_days: int = 7

    # Optional live data: AMFI publishes the latest NAV for every Indian
    # mutual fund scheme in a public text file. Disabled by default.
    amfi_enabled: bool = False
    amfi_nav_url: str = "https://www.amfiindia.com/spages/NAVAll.txt"
    amfi_timeout_s: float = 20.0
    amfi_cache_minutes: int = 360

    # Optional e-mail delivery for password reset.
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: SecretStr | None = None
    smtp_from: str = ""
    smtp_starttls: bool = True
    public_app_url: str = "http://localhost:5173"

    rate_limit_login_per_minute: int = 5
    rate_limit_register_per_hour: int = 10
    rate_limit_expensive_per_minute: int = 20

    log_level: str = "INFO"
    expose_api_docs: bool | None = None  # None -> docs on except in production

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @model_validator(mode="after")
    def _check_consistency(self) -> Settings:
        if "*" in self.cors_origins:
            raise ValueError("CORS_ORIGINS must list explicit origins, not '*'.")
        if self.llm_provider != "none" and not self.llm_model:
            raise ValueError("LLM_MODEL must be set when LLM_PROVIDER is enabled.")
        if self.llm_provider == "openai_compatible" and not self.llm_base_url:
            raise ValueError("LLM_BASE_URL is required for the openai_compatible provider.")
        if self.app_env == "production":
            urls = [self.database_url.get_secret_value()]
            if self.migration_database_url is not None:
                urls.append(self.migration_database_url.get_secret_value())
            if any("change-me" in url for url in urls):
                raise ValueError("The database password is still a placeholder from .env.example; "
                                 "set real passwords (run.sh / run.bat generate them) before starting.")
        return self

    @property
    def secure_cookies(self) -> bool:
        if self.cookie_secure is not None:
            return self.cookie_secure
        return self.app_env == "production"

    @property
    def api_docs_enabled(self) -> bool:
        if self.expose_api_docs is not None:
            return self.expose_api_docs
        return self.app_env != "production"

    @property
    def llm_configured(self) -> bool:
        if self.llm_provider == "none":
            return False
        if self.llm_provider == "anthropic":
            return self.llm_api_key is not None and bool(self.llm_api_key.get_secret_value())
        # Local OpenAI-compatible servers (Ollama, LM Studio) may not need a key.
        return bool(self.llm_base_url)

    @property
    def email_configured(self) -> bool:
        return bool(self.smtp_host and self.smtp_from)

    def secret_values(self) -> list[str]:
        """Every configured secret, used to scrub logs and model output."""
        values = [self.database_url.get_secret_value()]
        for secret in (self.migration_database_url, self.llm_api_key, self.smtp_password):
            if secret is not None and secret.get_secret_value():
                values.append(secret.get_secret_value())
        # Also scrub the password component of database URLs on its own.
        for url in list(values):
            if "://" in url and "@" in url:
                credentials = url.split("://", 1)[1].split("@", 1)[0]
                if ":" in credentials:
                    values.append(credentials.split(":", 1)[1])
        return [v for v in values if len(v) >= 6]


@lru_cache
def get_settings() -> Settings:
    return Settings()
