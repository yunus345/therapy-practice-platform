import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, status

from app.core.database import WriteSession
from app.modules.audit.repository import AuditRepository
from app.modules.audit.service import AuditService
from app.modules.auth.dependencies import (
    AuthServiceDep,
    ClockDep,
    RequestMetaDep,
    require_roles,
)
from app.modules.users.actor import Actor
from app.modules.users.admin_service import AdminService
from app.modules.users.models import User, UserRole
from app.modules.users.repository import UserRepository
from app.modules.users.schemas import AdminUserResponse, CreatePsychologistRequest
from app.modules.users.service import UserService

router = APIRouter(prefix="/admin", tags=["admin"])

AdminActor = Annotated[Actor, Depends(require_roles(UserRole.ADMIN))]


def get_admin_service(session: WriteSession, auth: AuthServiceDep, clock: ClockDep) -> AdminService:
    return AdminService(
        UserService(UserRepository(session), clock),
        auth,
        AuditService(AuditRepository(session)),
        clock,
    )


AdminServiceDep = Annotated[AdminService, Depends(get_admin_service)]


def _user_response(user: User) -> AdminUserResponse:
    return AdminUserResponse(id=user.id, role=user.role, is_active=user.is_active)


@router.post("/psychologists", status_code=status.HTTP_201_CREATED)
async def create_psychologist(
    body: CreatePsychologistRequest,
    actor: AdminActor,
    service: AdminServiceDep,
    meta: RequestMetaDep,
) -> AdminUserResponse:
    user = await service.create_psychologist(
        actor,
        email=body.email,
        first_name=body.first_name,
        last_name=body.last_name,
        slug=body.slug,
        title=body.title,
        ip_address=meta.ip_address,
    )
    return _user_response(user)


@router.post("/users/{user_id}/activate")
async def activate_user(
    user_id: uuid.UUID, actor: AdminActor, service: AdminServiceDep, meta: RequestMetaDep
) -> AdminUserResponse:
    user = await service.set_active(actor, user_id, active=True, ip_address=meta.ip_address)
    return _user_response(user)


@router.post("/users/{user_id}/deactivate")
async def deactivate_user(
    user_id: uuid.UUID, actor: AdminActor, service: AdminServiceDep, meta: RequestMetaDep
) -> AdminUserResponse:
    user = await service.set_active(actor, user_id, active=False, ip_address=meta.ip_address)
    return _user_response(user)


@router.post("/users/{user_id}/2fa/reset", status_code=status.HTTP_204_NO_CONTENT)
async def reset_two_factor(
    user_id: uuid.UUID, actor: AdminActor, service: AdminServiceDep, meta: RequestMetaDep
) -> None:
    await service.reset_two_factor(actor, user_id, ip_address=meta.ip_address)
