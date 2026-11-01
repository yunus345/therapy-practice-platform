"""Root router for version 1 of the API."""

from fastapi import APIRouter

from app.modules.health.router import router as health_router

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(health_router)
