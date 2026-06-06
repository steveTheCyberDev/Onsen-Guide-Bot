import os

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    openai_api_key: str
    anthropic_api_key: str
    google_maps_api_key: str
    rakuten_app_id: str
    rakuten_access_key: str
    rakuten_hotel_url: str
    # Shared secret required in the X-API-Key header on /chat and /hotels.
    # Defaults to "" which FAILS CLOSED: when unset, those endpoints reject every
    # request with 401. Must be set in every real environment (Railway, local .env,
    # and the frontend's matching VITE_API_KEY).
    api_key: str = ""
    # Allowed browser origins for CORS. Includes the local Vite dev server and the
    # deployed Vercel frontend. Override via env CORS_ORIGINS as a JSON array
    # (pydantic-settings parses list env vars as JSON), e.g.
    # CORS_ORIGINS='["https://onsen-guide-bot.vercel.app"]'. No trailing slashes.
    cors_origins: list[str] = [
        "http://localhost:5173",
        "https://onsen-guide-bot.vercel.app",
    ]
    # Filesystem path where ChromaDB persists. Default is relative ("chroma_db"),
    # which resolves to backend/chroma_db when run from the backend/ dir (local dev).
    # Override via env var CHROMA_PATH in production (Railway sets it to /app/chroma_db,
    # the mount point of the persistent volume) so the app and ingest job agree.
    chroma_path: str = "chroma_db"
    # Chat LLM used by the agent. Override via env var CHAT_MODEL (e.g. to swap
    # "gpt-4o" for "gpt-4o-mini" and measure the difference via the fabrication
    # eval at scripts/eval_fabrication.py).
    chat_model: str = "gpt-4o"

    # --- LangSmith step-level tracing (V2 Tier-1 instrumentation) ---
    # These surface the standard LangChain/LangSmith tracing env vars through one
    # source of truth. Tracing is OFF by default and FAILS SAFE: when
    # langsmith_tracing is False (the default) or no API key is present,
    # export_langsmith_env() exports nothing, so LangChain never attempts to
    # contact LangSmith and the app behaves exactly as before. Turn it on by
    # setting LANGSMITH_TRACING=true (or the legacy LANGCHAIN_TRACING_V2=true)
    # plus LANGSMITH_API_KEY in the environment.
    # Each field accepts both the modern LANGSMITH_* name and the legacy
    # LANGCHAIN_* alias (LANGCHAIN_TRACING_V2 etc.) so either works in the env.
    langsmith_tracing: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "LANGSMITH_TRACING", "LANGSMITH_TRACING_V2", "LANGCHAIN_TRACING_V2"
        ),
    )
    langsmith_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("LANGSMITH_API_KEY", "LANGCHAIN_API_KEY"),
    )
    # Project name traces are grouped under in the LangSmith UI. Keep this stable
    # so the GPT-4o ReAct baseline is easy to find later and compare against the
    # slot-filling/workflow migration.
    langsmith_project: str = Field(
        default="onsen-guide-bot",
        validation_alias=AliasChoices("LANGSMITH_PROJECT", "LANGCHAIN_PROJECT"),
    )
    # LangSmith ingestion endpoint. Default is the US SaaS endpoint; override for
    # the EU region ("https://eu.api.smith.langchain.com") or a self-hosted host.
    langsmith_endpoint: str = Field(
        default="https://api.smith.langchain.com",
        validation_alias=AliasChoices("LANGSMITH_ENDPOINT", "LANGCHAIN_ENDPOINT"),
    )

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        # Accept both the LANGSMITH_* names above and the legacy LANGCHAIN_*
        # aliases (LANGCHAIN_TRACING_V2, LANGCHAIN_API_KEY, ...) so existing
        # tooling/docs keep working. Defined per-field via validation_alias.
        "extra": "ignore",
    }


settings = Settings()


def export_langsmith_env() -> bool:
    """Export LangSmith tracing settings into ``os.environ`` if enabled.

    LangChain's tracer reads ``LANGSMITH_*`` / ``LANGCHAIN_*`` environment
    variables directly (langsmith caches them via ``lru_cache``), so they must be
    present in ``os.environ`` before the agent first runs. Call this once at the
    agent/config layer at import time.

    No-op (and returns ``False``) unless ``langsmith_tracing`` is True AND an API
    key is set — this keeps tracing strictly opt-in and prevents broken local/prod
    runs when keys are absent. Does not overwrite any tracing vars already present
    in the real environment (so an operator's explicit override wins).

    Returns:
        True if tracing env vars were exported (tracing active), else False.
    """
    if not settings.langsmith_tracing or not settings.langsmith_api_key:
        return False
    # setdefault: never clobber an explicit value already in the environment.
    os.environ.setdefault("LANGSMITH_TRACING", "true")
    os.environ.setdefault("LANGSMITH_API_KEY", settings.langsmith_api_key)
    os.environ.setdefault("LANGSMITH_PROJECT", settings.langsmith_project)
    os.environ.setdefault("LANGSMITH_ENDPOINT", settings.langsmith_endpoint)
    return True
