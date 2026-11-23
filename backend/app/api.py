"""Root router for version 1 of the API."""

from fastapi import APIRouter, Depends

from app.core.errors import ERROR_RESPONSES
from app.modules.auth.csrf import verify_csrf
from app.modules.auth.router import router as auth_router
from app.modules.catalog.router import router as catalog_router
from app.modules.clients.router import router as clients_router
from app.modules.health.router import router as health_router
from app.modules.scheduling.router import router as scheduling_router
from app.modules.users.router import profile_router
from app.modules.users.router import router as admin_router

api_router = APIRouter(
    prefix="/api/v1", dependencies=[Depends(verify_csrf)], responses=ERROR_RESPONSES
)
api_router.include_router(health_router)
api_router.include_router(auth_router)
api_router.include_router(admin_router)
api_router.include_router(profile_router)
api_router.include_router(catalog_router)
api_router.include_router(clients_router)
api_router.include_router(scheduling_router)
