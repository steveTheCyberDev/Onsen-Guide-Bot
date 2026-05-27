import logging

from fastapi import APIRouter
from pydantic import BaseModel

from agent.agent import run_agent, HotelResult, OnsenResult

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
    result = await run_agent(request.message, request.session_id)
    logger.info("POST /chat response | session_id=%s | reply=%r | hotels=%d", request.session_id, result["reply"], len(result["hotels"]))
    return ChatResponse(**result)
