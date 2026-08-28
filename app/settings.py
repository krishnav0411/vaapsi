"""Env-driven settings. Real values live in `.env` (gitignored) — never in code."""

from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    app_env: str = "development"
    data_dir: Path = BASE_DIR / "data"

    # Razorpay TEST-mode credentials (values only ever in .env)
    razorpay_key_id: str = ""
    razorpay_key_secret: str = ""
    razorpay_webhook_secret: str = ""

    # ngrok (Day 0 tunnel)
    ngrok_authtoken: str = ""

    # LLM adapter (Day 3): env lookup accepts the VAAPSI_-prefixed spelling
    # documented in .env.example first, bare LLM_* names as fallback — same
    # aliasing approach as kill_switch, so .env and code can never disagree
    # about the variable name.
    llm_base_url: str = Field(
        default="https://api.openai.com/v1",
        validation_alias=AliasChoices("VAAPSI_LLM_BASE_URL", "LLM_BASE_URL"),
    )
    llm_model: str = Field(
        default="gpt-4o-mini",
        validation_alias=AliasChoices("VAAPSI_LLM_MODEL", "LLM_MODEL"),
    )
    llm_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("VAAPSI_LLM_API_KEY", "LLM_API_KEY"),
    )

    # Kill switch: VAAPSI_KILL_SWITCH=1 → policy denies ALL outbound actions,
    # ledger/health mode flips to KILLED. Default off — normal operation.
    kill_switch: bool = Field(default=False, validation_alias="VAAPSI_KILL_SWITCH")

    # Public demo mode: VAAPSI_PUBLIC_DEMO=1 → fail-closed read-only demo.
    # app/demo_mode.py refuses to boot when real credentials are also set,
    # app.main skips the ingest router and 404s every write route, and a
    # missing store is seeded with sanitized demo data on boot. Default off.
    public_demo: bool = Field(default=False, validation_alias="VAAPSI_PUBLIC_DEMO")

    # Where the D5 dashboard's kill endpoint leaves its operator note
    # (a commented line — the in-memory flip rules the running process;
    # tests point this at a tmp copy so the real .env is never touched).
    env_file_path: Path = BASE_DIR / ".env"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "vaapsi.sqlite3"

    @property
    def archive_dir(self) -> Path:
        return self.data_dir / "webhook_archive"


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.archive_dir.mkdir(parents=True, exist_ok=True)
    return settings
