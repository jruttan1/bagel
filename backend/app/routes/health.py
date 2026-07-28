from fastapi import APIRouter
from sqlalchemy import text

from app.dependencies import DbSession
from app.schemas import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health/live", response_model=HealthResponse)
async def live() -> HealthResponse:
    return HealthResponse(status="ok")


@router.get("/health/ready", response_model=HealthResponse)
async def ready(session: DbSession) -> HealthResponse:
    await session.execute(text("SELECT 1"))
    return HealthResponse(status="ok", database="ok")
