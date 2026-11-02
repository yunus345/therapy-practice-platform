import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, status

from app.core.database import WriteSession
from app.modules.auth.dependencies import (
    AdminActor,
    AuthServiceDep,
    ClockDep,
    PsychologistActor,
    RequestMetaDep,
)
from app.modules.users.admin_service import AdminService, build_admin_service
from app.modules.users.models import User
from app.modules.users.profile_service import ProfileService, build_profile_service
from app.modules.users.schemas import (
    AdminUserResponse,
    CreatePsychologistRequest,
    ProfileResponse,
    UpdateProfileRequest,
)

router = APIRouter(prefix="/admin", tags=["admin"])
profile_router = APIRouter(prefix="/staff/profile", tags=["profile"])


def get_admin_service(session: WriteSession, auth: AuthServiceDep, clock: ClockDep) -> AdminService:
    return build_admin_service(session, auth, clock)


def get_profile_service(session: WriteSession, clock: ClockDep) -> ProfileService:
    return build_profile_service(session, clock)


AdminServiceDep = Annotated[AdminService, Depends(get_admin_service)]
ProfileServiceDep = Annotated[ProfileService, Depends(get_profile_service)]


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


@profile_router.get("")
async def get_profile(actor: PsychologistActor, service: ProfileServiceDep) -> ProfileResponse:
    return ProfileResponse.model_validate(await service.get_own(actor))


@profile_router.patch("")
async def update_profile(
    body: UpdateProfileRequest,
    actor: PsychologistActor,
    service: ProfileServiceDep,
    meta: RequestMetaDep,
) -> ProfileResponse:
    profile = await service.update_own(actor, body, ip_address=meta.ip_address)
    return ProfileResponse.model_validate(profile)
