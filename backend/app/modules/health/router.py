from fastapi import APIRouter, Depends

from app.core.database import DatabaseDep
from app.core.rate_limit import RateLimit
from app.modules.auth.dependencies import Public
from app.modules.health import service
from app.modules.health.schemas import HealthResponse

router = APIRouter(tags=["health"])


@router.get(
    "/health",
    dependencies=[Public, Depends(RateLimit("health", limit=120, window_seconds=60))],
)
async def get_health(database: DatabaseDep) -> HealthResponse:
    await service.check_health(database)
    return HealthResponse()
