from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    openai_api_key: str
    anthropic_api_key: str
    google_maps_api_key: str
    rakuten_app_id: str
    rakuten_access_key: str
    rakuten_hotel_url: str
    cors_origins: list[str] = ["http://localhost:5173"]
    # Filesystem path where ChromaDB persists. Default is relative ("chroma_db"),
    # which resolves to backend/chroma_db when run from the backend/ dir (local dev).
    # Override via env var CHROMA_PATH in production (Railway sets it to /app/chroma_db,
    # the mount point of the persistent volume) so the app and ingest job agree.
    chroma_path: str = "chroma_db"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
