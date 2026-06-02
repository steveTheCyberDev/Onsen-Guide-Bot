import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

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

app.include_router(chat.router)
app.include_router(hotels.router)


@app.get("/health")
def health():
    return {"status": "ok"}
