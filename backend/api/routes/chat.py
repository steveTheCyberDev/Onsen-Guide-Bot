import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from agent.agent import run_agent, HotelResult, OnsenResult
from api.limiter import limiter
from core.config import settings
from core.exceptions import OnsenBotError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["chat"])


class ChatRequest(BaseModel):
    message: str
    session_id: str = "default"


class ChatResponse(BaseModel):
    reply: str
    onsens: list[OnsenResult] = []
    hotels: list[HotelResult] = []
    # V2.5 RECOMMEND addition (ADDITIVE, optional). Carries the recommend-mode
    # top-level pick; None for search/ask modes and the legacy ReAct path so the
    # existing contract is unchanged for those flows.
    recommendation: str | None = None


# Rate-limited per client IP (paid endpoint). The limit string comes from
# settings.rate_limit_chat (env RATE_LIMIT_CHAT). slowapi requires the `request:
# Request` parameter to read the client key; the parsed body stays in `payload`.
@router.post("", response_model=ChatResponse)
@limiter.limit(settings.rate_limit_chat)
async def chat(request: Request, payload: ChatRequest):
    logger.info("POST /chat request | session_id=%s | message=%r", payload.session_id, payload.message)
    try:
        result = await run_agent(payload.message, payload.session_id)
    except OnsenBotError as e:
        logger.error("POST /chat service error | session_id=%s | error=%s", payload.session_id, e)
        raise HTTPException(status_code=502, detail=str(e))
    except Exception as e:
        logger.exception("POST /chat unexpected error | session_id=%s", payload.session_id)
        raise HTTPException(status_code=500, detail="An unexpected error occurred.")
    logger.info("POST /chat response | session_id=%s | reply=%r | hotels=%d", payload.session_id, result["reply"], len(result["hotels"]))
    return ChatResponse(**result)
