"""Environment-based configuration. Every setting can be overridden by an environment variable.

Models, caps and verdict thresholds live here, not in code, so an experiment is a config diff.
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(BACKEND_DIR / ".env",), extra="ignore")

    app_env: Literal["development", "test", "production"] = "development"
    database_url: str = "postgresql+psycopg://research:research@localhost:5436/research"

    # Gemini (server-side only). GEMINI_MODELS is an ordered fallback list: when one model's daily
    # quota is used up the gateway moves to the next one.
    gemini_api_key: str | None = None
    gemini_models: str = "gemini-3.5-flash-lite,gemini-3.1-flash-lite"
    gemini_thinking_level: Literal["minimal", "low", "medium", "high"] = "low"
    model_timeout_seconds: float = Field(default=90, ge=5, le=300)
    model_max_attempts: int = Field(default=3, ge=1, le=5)
    daily_model_call_limit: int = Field(default=200, ge=0)
    # List prices in USD per 1M tokens, used to turn real token counts into cost. Free-tier calls are
    # billed $0; this is the price the same tokens would cost on a paid key. Verify against the
    # provider's pricing page before quoting it.
    price_input_per_mtok: float = Field(default=0.10, ge=0)
    price_output_per_mtok: float = Field(default=0.40, ge=0)

    # Search and fetch
    search_provider: Literal["auto", "tavily", "wikipedia"] = "auto"
    tavily_api_key: str | None = None
    # Wikimedia (and polite crawling generally) requires a User-Agent with a contact URL.
    http_user_agent: str = "DeepResearchAgent/1.0 (+https://github.com/0shunya; research bot)"
    search_results_per_query: int = Field(default=5, ge=1, le=10)
    fetch_timeout_seconds: float = Field(default=12, ge=2, le=60)
    fetch_max_bytes: int = Field(default=3_000_000, ge=50_000)
    page_chars_for_model: int = Field(default=6000, ge=1000, le=40000)
    allow_private_fetch: bool = False  # tests only; production must never fetch internal addresses

    # Caps on every run (the roadmap's "runaway agent" guard)
    max_sub_questions: int = Field(default=5, ge=1, le=8)
    max_parallel_workers: int = Field(default=3, ge=1, le=8)
    max_steps_per_agent: int = Field(default=2, ge=1, le=5)
    max_task_attempts: int = Field(default=2, ge=1, le=5)
    default_budget_tokens: int = Field(default=120_000, ge=5_000)
    max_budget_tokens: int = Field(default=400_000, ge=5_000)
    research_budget_share: float = Field(default=0.7, gt=0, le=1)  # research stops here; rest is for verify + write
    run_timeout_seconds: int = Field(default=600, ge=30)

    # Verdict rule
    min_independent_domains: int = Field(default=2, ge=1, le=5)
    strong_contradiction_score: float = Field(default=0.7, ge=0, le=1)
    verifier_batch_size: int = Field(default=30, ge=5, le=80)

    # Browser access
    frontend_origin: str = "http://localhost:5183"
    demo_access_code: str | None = None
    runs_per_hour_per_client: int = Field(default=10, ge=1)

    # Background execution: "inline" runs the orchestrator inside the API process; "off" for tests.
    worker_mode: Literal["inline", "off"] = "inline"

    @field_validator("database_url")
    @classmethod
    def _normalize_db_url(cls, value: str) -> str:
        # Managed hosts hand out postgres:// or postgresql:// URLs; SQLAlchemy needs the psycopg driver name.
        for prefix in ("postgres://", "postgresql://"):
            if value.startswith(prefix):
                return "postgresql+psycopg://" + value[len(prefix):]
        return value

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip().rstrip("/") for origin in self.frontend_origin.split(",") if origin.strip()]

    @property
    def model_list(self) -> list[str]:
        return [m.strip() for m in self.gemini_models.split(",") if m.strip()]

    @property
    def gemini_configured(self) -> bool:
        return bool(self.gemini_api_key)

    @property
    def resolved_search_provider(self) -> str:
        if self.search_provider == "auto":
            return "tavily" if self.tavily_api_key else "wikipedia"
        return self.search_provider


@lru_cache
def get_settings() -> Settings:
    return Settings()
