from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    openai_api_key: str
    anthropic_api_key: str
    google_maps_api_key: str
    rakuten_app_id: str
    rakuten_access_key: str
    rakuten_hotel_url: str

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
