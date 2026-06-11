import os
from pathlib import Path

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
    # Filesystem path to the onsen data directory (the *_springs.jsonl files the
    # ingest scripts read). Default is "" which means "use the computed local
    # default" — backend/data, resolved from this file's own location so it is
    # CWD-independent. Override via env var DATA_PATH in production (Railway/Docker
    # sets it to /app/data, where COPY data/ data/ lands) so the ingest job finds
    # the files regardless of the source-tree layout. Mirrors chroma_path above.
    data_path: str = ""
    # Chat LLM used by the agent. Override via env var CHAT_MODEL (e.g. to swap
    # "gpt-4o" for "gpt-4o-mini" and measure the difference via the fabrication
    # eval at scripts/eval_fabrication.py).
    chat_model: str = "gpt-4o"
    # Which engine the /chat dispatcher (agent/agent.py::run_agent) routes to.
    # "react"    → the legacy GPT-4o ReAct agent (run_react_agent) — current live behavior.
    # "workflow" → the deterministic V2 pipeline (agent/workflow/pipeline.py::run_workflow).
    # Default MUST stay "react" so live behavior is unchanged until explicitly flipped.
    # This is the A/B + instant-rollback seam. Override via env var CHAT_ENGINE.
    chat_engine: str = "react"
    # Small, fast LLM used by the V2 workflow's intent-parsing node
    # (agent/workflow/intent.py) to extract {prefecture, query, wants_hotels}.
    # Kept separate from chat_model so the cheap routing call can use a smaller
    # model (gpt-4o-mini) than the main chat/analyze path. Override via env var
    # INTENT_MODEL.
    intent_model: str = "gpt-4o-mini"
    # LLM used by the V2.5 RECOMMEND brain (agent/workflow/analyze.py::analyze_onsen)
    # to produce grounded per-onsen pros/cons + a top-level recommendation. Kept
    # separate from intent_model so the cheap routing hop and the heavier judgement
    # hop can use different models. Override via env var ANALYZE_MODEL.
    analyze_model: str = "gpt-4o"
    # Gate for the RECOMMEND brain. When False (default) the recommend branch skips
    # the analyze_onsen LLM call and returns candidates without pros/cons (safe/dead
    # until flipped) — this is the A/B rollout seam, mirroring chat_engine above.
    # Override via env var ANALYZE_ENABLED.
    analyze_enabled: bool = False
    # Separate ChromaDB collection for Layer 2 KB prose (etiquette, spring-type
    # benefits, …). Kept apart from the onsen_springs collection so an onsen search
    # never retrieves an etiquette chunk and vice-versa. Override via KB_COLLECTION.
    kb_collection: str = "onsen_knowledge"
    # Directory holding the Layer 2 markdown docs. Default "" → computed local
    # default data_dir/knowledge (CWD-independent, from __file__). Override via
    # KB_DATA_PATH in prod (Railway ships them under /app/data/knowledge).
    kb_data_path: str = ""
    # Gate for ask mode (Layer 2 semantic RAG). False (default) → ask returns the
    # safe "coming soon" stub; True → real grounded RAG answer. A/B + instant
    # rollback seam, mirrors analyze_enabled above. Override via ASK_ENABLED.
    ask_enabled: bool = False
    # Top-k KB chunks retrieved for an ask answer. Override via ASK_TOP_K.
    ask_top_k: int = 4
    # Cosine-DISTANCE ceiling for KB chunks (Chroma returns distance, lower = closer).
    # Chunks above this are dropped; if nothing survives → the "I don't know" path.
    # Override via ASK_MAX_DISTANCE.
    ask_max_distance: float = 0.55
    # LLM that writes the grounded ask answer. Reuse intent_model by DEFAULT (cheap;
    # the answer is short and fully grounded), env-overridable to a stronger model.
    # "" → fall back to settings.intent_model at the call site. Override via ASK_MODEL.
    ask_model: str = ""
    # Bounded retry count for outbound LLM calls (ChatOpenAI). Passed as
    # `max_retries` to every ChatOpenAI instance (the ReAct llm in agent/agent.py
    # and the intent llm in agent/workflow/intent.py) so transient OpenAI errors
    # (timeouts, 429/5xx) are retried a few times instead of failing the request,
    # without retrying forever. Override via env var LLM_MAX_RETRIES.
    llm_max_retries: int = 2
    # --- Inbound rate limiting (slowapi) ---
    # Per-client-IP limits applied to the PAID endpoints only (POST /chat and
    # POST /hotels); /health and other infra routes stay unlimited. Values use
    # slowapi's limit-string syntax ("<count>/<period>", e.g. "20/minute").
    # Override via env vars RATE_LIMIT_CHAT / RATE_LIMIT_HOTELS.
    # Storage is in-memory (slowapi default), correct for the current single-worker
    # Dockerfile (uvicorn --workers 1). TODO: a multi-instance / multi-worker
    # deploy needs a shared store (slowapi storage_uri → Redis); not built now.
    rate_limit_chat: str = "20/minute"
    rate_limit_hotels: str = "60/minute"
    # Deployment environment label for trace/log filtering. Default is "local";
    # Railway sets APP_ENV=production so traces/logs from the deployed app can be
    # told apart from local runs. Override via env var APP_ENV.
    app_env: str = "local"
    # Release/deploy identifier so traces correlate with a specific deploy.
    # Resolution order: an explicit APP_VERSION wins; otherwise fall back to
    # Railway's auto-injected RAILWAY_GIT_COMMIT_SHA (full 40-char SHA, present
    # on GitHub-deployed services); local default is "dev". Same AliasChoices
    # pattern as the LangSmith settings below.
    app_version: str = Field(
        default="dev",
        validation_alias=AliasChoices("APP_VERSION", "RAILWAY_GIT_COMMIT_SHA"),
    )

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

    @property
    def data_dir(self) -> Path:
        """Resolved onsen data directory.

        Returns ``Path(data_path)`` when DATA_PATH is set (prod override), else the
        backend-relative ``data/`` resolved from this config file's own location
        (config.py lives at backend/core/config.py, so parent.parent == backend/).
        Resolving from __file__ keeps it correct no matter the current working dir.
        """
        if self.data_path:
            return Path(self.data_path)
        return Path(__file__).resolve().parent.parent / "data"

    @property
    def kb_data_dir(self) -> Path:
        """Resolved Layer 2 knowledge-base markdown directory.

        Returns ``Path(kb_data_path)`` when KB_DATA_PATH is set (prod override),
        else ``data_dir / "knowledge"`` — a sibling of the onsen data dir, so the
        existing data_dir env-split (and the Railway COPY data/ layout) cover it
        for free. Mirrors the data_dir property above.
        """
        if self.kb_data_path:
            return Path(self.kb_data_path)
        return self.data_dir / "knowledge"

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
