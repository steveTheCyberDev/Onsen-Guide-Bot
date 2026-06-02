import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from agent.agent import run_agent, HotelResult, OnsenResult
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


@router.post("", response_model=ChatResponse)
async def chat(request: ChatRequest):
    logger.info("POST /chat request | session_id=%s | message=%r", request.session_id, request.message)
    try:
        result = await run_agent(request.message, request.session_id)
    except OnsenBotError as e:
        logger.error("POST /chat service error | session_id=%s | error=%s", request.session_id, e)
        raise HTTPException(status_code=502, detail=str(e))
    except Exception as e:
        logger.exception("POST /chat unexpected error | session_id=%s", request.session_id)
        raise HTTPException(status_code=500, detail="An unexpected error occurred.")
    logger.info("POST /chat response | session_id=%s | reply=%r | hotels=%d", request.session_id, result["reply"], len(result["hotels"]))
    return ChatResponse(**result)
