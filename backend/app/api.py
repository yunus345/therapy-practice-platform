"""Root router for version 1 of the API."""

from fastapi import APIRouter, Depends

from app.modules.auth.csrf import verify_csrf
from app.modules.auth.router import router as auth_router
from app.modules.health.router import router as health_router
from app.modules.users.router import router as admin_router

api_router = APIRouter(prefix="/api/v1", dependencies=[Depends(verify_csrf)])
api_router.include_router(health_router)
api_router.include_router(auth_router)
api_router.include_router(admin_router)
