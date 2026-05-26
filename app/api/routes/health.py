"""Health check endpoints — used by Railway/Render for liveness probes."""
from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: str
    version: str = "1.0.0"
    service: str = "meetingmind"


@router.get("/health", response_model=HealthResponse)
async def health_check():
    return HealthResponse(status="ok")


@router.get("/")
async def root():
    return {
        "service": "MeetingMind API",
        "version": "1.0.0",
        "docs": "/docs",
    }
