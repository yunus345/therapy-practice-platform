import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, status

from app.core.database import ReadSession, WriteSession
from app.core.pagination import Page, PageParamsDep
from app.core.rate_limit import RateLimit
from app.core.schemas import NO_NUL_PATTERN, UtcDatetime
from app.modules.auth.dependencies import (
    ClientActor,
    ClockDep,
    PsychologistActor,
    Public,
    RequestMetaDep,
    SettingsDep,
    StaffActor,
    session_rate_limit_key,
)
from app.modules.scheduling.models import AppointmentStatus, AppointmentWhen
from app.modules.scheduling.schemas import (
    AvailabilityRuleResponse,
    BookAppointmentRequest,
    ChangeAppointmentStatusRequest,
    ClientAppointmentResponse,
    CreateAvailabilityRuleRequest,
    CreateStaffAppointmentRequest,
    CreateTimeOffRequest,
    PublicProfileResponse,
    RescheduleAppointmentRequest,
    SlotsResponse,
    StaffAppointmentResponse,
    TimeOffResponse,
    UpdateAvailabilityRuleRequest,
    UpdateTimeOffRequest,
)
from app.modules.scheduling.service import SchedulingService, build_scheduling_service

router = APIRouter(tags=["scheduling"])

ONE_MINUTE = 60
ONE_HOUR = 3600

PublicSlug = Annotated[str, Path(max_length=100, pattern=NO_NUL_PATTERN)]


def get_scheduling_service(
    session: WriteSession, settings: SettingsDep, clock: ClockDep
) -> SchedulingService:
    return build_scheduling_service(session, settings, clock)


def get_public_scheduling_service(
    session: ReadSession, settings: SettingsDep, clock: ClockDep
) -> SchedulingService:
    """A session that never commits, so that anonymous requests cannot change anything."""
    return build_scheduling_service(session, settings, clock)


SchedulingServiceDep = Annotated[SchedulingService, Depends(get_scheduling_service)]
PublicSchedulingDep = Annotated[SchedulingService, Depends(get_public_scheduling_service)]


@router.get("/staff/availability-rules")
async def list_availability_rules(
    actor: StaffActor,
    scheduling: SchedulingServiceDep,
    page: PageParamsDep,
    psychologist_id: uuid.UUID | None = None,
) -> Page[AvailabilityRuleResponse]:
    rules, total = await scheduling.list_rules(actor, psychologist_id, page)
    return Page[AvailabilityRuleResponse].of(
        [AvailabilityRuleResponse.model_validate(rule) for rule in rules], total, page
    )


@router.post("/staff/availability-rules", status_code=status.HTTP_201_CREATED)
async def create_availability_rule(
    body: CreateAvailabilityRuleRequest,
    actor: PsychologistActor,
    scheduling: SchedulingServiceDep,
    meta: RequestMetaDep,
) -> AvailabilityRuleResponse:
    rule = await scheduling.create_rule(actor, body, ip_address=meta.ip_address)
    return AvailabilityRuleResponse.model_validate(rule)


@router.patch("/staff/availability-rules/{rule_id}")
async def update_availability_rule(
    rule_id: uuid.UUID,
    body: UpdateAvailabilityRuleRequest,
    actor: PsychologistActor,
    scheduling: SchedulingServiceDep,
    meta: RequestMetaDep,
) -> AvailabilityRuleResponse:
    rule = await scheduling.update_rule(actor, rule_id, body, ip_address=meta.ip_address)
    return AvailabilityRuleResponse.model_validate(rule)


@router.delete("/staff/availability-rules/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_availability_rule(
    rule_id: uuid.UUID,
    actor: PsychologistActor,
    scheduling: SchedulingServiceDep,
    meta: RequestMetaDep,
) -> None:
    await scheduling.delete_rule(actor, rule_id, ip_address=meta.ip_address)


@router.get("/staff/time-off")
async def list_time_off(
    actor: StaffActor,
    scheduling: SchedulingServiceDep,
    page: PageParamsDep,
    psychologist_id: uuid.UUID | None = None,
    start: Annotated[UtcDatetime | None, Query(alias="from")] = None,
    end: Annotated[UtcDatetime | None, Query(alias="to")] = None,
) -> Page[TimeOffResponse]:
    periods, total = await scheduling.list_time_off(
        actor, psychologist_id, page, start=start, end=end
    )
    return Page[TimeOffResponse].of(
        [TimeOffResponse.model_validate(period) for period in periods], total, page
    )


@router.post("/staff/time-off", status_code=status.HTTP_201_CREATED)
async def create_time_off(
    body: CreateTimeOffRequest,
    actor: PsychologistActor,
    scheduling: SchedulingServiceDep,
    meta: RequestMetaDep,
) -> TimeOffResponse:
    time_off = await scheduling.create_time_off(actor, body, ip_address=meta.ip_address)
    return TimeOffResponse.model_validate(time_off)


@router.patch("/staff/time-off/{time_off_id}")
async def update_time_off(
    time_off_id: uuid.UUID,
    body: UpdateTimeOffRequest,
    actor: PsychologistActor,
    scheduling: SchedulingServiceDep,
    meta: RequestMetaDep,
) -> TimeOffResponse:
    time_off = await scheduling.update_time_off(
        actor, time_off_id, body, ip_address=meta.ip_address
    )
    return TimeOffResponse.model_validate(time_off)


@router.delete("/staff/time-off/{time_off_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_time_off(
    time_off_id: uuid.UUID,
    actor: PsychologistActor,
    scheduling: SchedulingServiceDep,
    meta: RequestMetaDep,
) -> None:
    await scheduling.delete_time_off(actor, time_off_id, ip_address=meta.ip_address)


@router.get(
    "/public/psychologists/{slug}",
    dependencies=[Public, Depends(RateLimit("public.profile", 60, ONE_MINUTE))],
)
async def get_public_profile(
    slug: PublicSlug, scheduling: PublicSchedulingDep
) -> PublicProfileResponse:
    return PublicProfileResponse.model_validate(await scheduling.get_public_profile(slug))


@router.get(
    "/public/psychologists/{slug}/slots",
    dependencies=[Public, Depends(RateLimit("public.slots", 60, ONE_MINUTE))],
)
async def get_public_slots(
    slug: PublicSlug,
    service_id: uuid.UUID,
    from_date: Annotated[date, Query(alias="from")],
    to_date: Annotated[date, Query(alias="to")],
    scheduling: PublicSchedulingDep,
) -> SlotsResponse:
    slots = await scheduling.get_public_slots(slug, service_id, from_date, to_date)
    return SlotsResponse.model_validate(slots)


@router.post(
    "/appointments",
    status_code=status.HTTP_201_CREATED,
    dependencies=[
        Depends(RateLimit("appointments.book", 20, ONE_HOUR, key=session_rate_limit_key))
    ],
)
async def book_appointment(
    body: BookAppointmentRequest,
    actor: ClientActor,
    scheduling: SchedulingServiceDep,
    meta: RequestMetaDep,
) -> ClientAppointmentResponse:
    appointment = await scheduling.book_appointment(actor, body, ip_address=meta.ip_address)
    return ClientAppointmentResponse.model_validate(appointment)


@router.get("/me/appointments")
async def list_own_appointments(
    actor: ClientActor,
    scheduling: SchedulingServiceDep,
    page: PageParamsDep,
    when: AppointmentWhen = AppointmentWhen.ALL,
) -> Page[ClientAppointmentResponse]:
    appointments, total = await scheduling.list_own_appointments(actor, when, page)
    return Page[ClientAppointmentResponse].of(
        [ClientAppointmentResponse.model_validate(item) for item in appointments], total, page
    )


@router.post("/appointments/{appointment_id}/cancel")
async def cancel_own_appointment(
    appointment_id: uuid.UUID,
    actor: ClientActor,
    scheduling: SchedulingServiceDep,
    meta: RequestMetaDep,
) -> ClientAppointmentResponse:
    appointment = await scheduling.cancel_own_appointment(
        actor, appointment_id, ip_address=meta.ip_address
    )
    return ClientAppointmentResponse.model_validate(appointment)


@router.post("/appointments/{appointment_id}/reschedule", status_code=status.HTTP_201_CREATED)
async def reschedule_own_appointment(
    appointment_id: uuid.UUID,
    body: RescheduleAppointmentRequest,
    actor: ClientActor,
    scheduling: SchedulingServiceDep,
    meta: RequestMetaDep,
) -> ClientAppointmentResponse:
    appointment = await scheduling.reschedule_own_appointment(
        actor, appointment_id, body, ip_address=meta.ip_address
    )
    return ClientAppointmentResponse.model_validate(appointment)


@router.get("/staff/appointments")
async def list_appointments(
    actor: StaffActor,
    scheduling: SchedulingServiceDep,
    page: PageParamsDep,
    psychologist_id: uuid.UUID | None = None,
    start: Annotated[UtcDatetime | None, Query(alias="from")] = None,
    end: Annotated[UtcDatetime | None, Query(alias="to")] = None,
    statuses: Annotated[list[AppointmentStatus] | None, Query(alias="status")] = None,
) -> Page[StaffAppointmentResponse]:
    appointments, total = await scheduling.list_appointments(
        actor,
        page,
        psychologist_id=psychologist_id,
        start=start,
        end=end,
        statuses=statuses or (),
    )
    return Page[StaffAppointmentResponse].of(
        [StaffAppointmentResponse.model_validate(item) for item in appointments], total, page
    )


@router.post("/staff/appointments", status_code=status.HTTP_201_CREATED)
async def create_appointment(
    body: CreateStaffAppointmentRequest,
    actor: PsychologistActor,
    scheduling: SchedulingServiceDep,
    meta: RequestMetaDep,
) -> StaffAppointmentResponse:
    appointment = await scheduling.create_appointment(actor, body, ip_address=meta.ip_address)
    return StaffAppointmentResponse.model_validate(appointment)


@router.get("/staff/appointments/{appointment_id}")
async def get_appointment(
    appointment_id: uuid.UUID, actor: StaffActor, scheduling: SchedulingServiceDep
) -> StaffAppointmentResponse:
    appointment = await scheduling.get_appointment(actor, appointment_id)
    return StaffAppointmentResponse.model_validate(appointment)


@router.post("/staff/appointments/{appointment_id}/status")
async def change_appointment_status(
    appointment_id: uuid.UUID,
    body: ChangeAppointmentStatusRequest,
    actor: PsychologistActor,
    scheduling: SchedulingServiceDep,
    meta: RequestMetaDep,
) -> StaffAppointmentResponse:
    appointment = await scheduling.change_status(
        actor, appointment_id, body, ip_address=meta.ip_address
    )
    return StaffAppointmentResponse.model_validate(appointment)


@router.post("/staff/appointments/{appointment_id}/reschedule", status_code=status.HTTP_201_CREATED)
async def reschedule_appointment(
    appointment_id: uuid.UUID,
    body: RescheduleAppointmentRequest,
    actor: PsychologistActor,
    scheduling: SchedulingServiceDep,
    meta: RequestMetaDep,
) -> StaffAppointmentResponse:
    appointment = await scheduling.reschedule_appointment(
        actor, appointment_id, body, ip_address=meta.ip_address
    )
    return StaffAppointmentResponse.model_validate(appointment)
