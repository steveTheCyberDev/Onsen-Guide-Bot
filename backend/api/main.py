import logging

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.dependencies import require_api_key
from api.routes import chat, hotels
from core.config import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)

app = FastAPI(title="Onsen Guide Bot")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(chat.router, dependencies=[Depends(require_api_key)])
app.include_router(hotels.router, dependencies=[Depends(require_api_key)])


@app.get("/health")
def health():
    # Intentionally unauthenticated — Railway healthchecks hit this without a key.
    return {"status": "ok"}
