import logging

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi.errors import RateLimitExceeded

from api.dependencies import require_api_key
from api.limiter import limiter
from api.routes import chat, hotels
from core.config import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)

logger = logging.getLogger(__name__)

app = FastAPI(title="Onsen Guide Bot")

# Inbound rate limiting (slowapi). The shared Limiter is attached to app.state
# (slowapi reads it from there) and we register a handler so exceeding a route's
# per-IP limit returns HTTP 429 with a clear JSON body. CORS still applies: the
# CORSMiddleware below wraps the whole app, so it adds its headers to this 429
# response too. Limits are declared per-route on /chat and /hotels only; /health
# carries no limiter and stays unlimited.
app.state.limiter = limiter


@app.exception_handler(RateLimitExceeded)
async def _rate_limit_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    logger.warning("rate limit exceeded | path=%s | limit=%s", request.url.path, str(exc.limit.limit))
    return JSONResponse(status_code=429, content={"detail": "rate limit exceeded"})


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
