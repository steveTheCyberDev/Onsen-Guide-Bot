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

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
