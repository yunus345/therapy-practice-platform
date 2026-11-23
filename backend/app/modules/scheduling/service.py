"""Working schedules, the public view of them, and appointments.

Methods that take an actor enforce its role and scope. The `get_public_*` methods take
none, because what they return is meant for anyone, so it must never hold anything else.
"""

import uuid
from collections.abc import Awaitable, Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from functools import partial
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import Clock
from app.core.config import Settings
from app.core.errors import AppError, ErrorCode, NotFoundError
from app.core.models import changed_values
from app.core.pagination import PageParams
from app.modules.audit.service import AuditAction, AuditService, build_audit_service
from app.modules.catalog.models import DeliveryMode
from app.modules.catalog.service import (
    CatalogService,
    OfferedService,
    ServiceSummary,
    build_catalog_service,
)
from app.modules.clients.models import Client
from app.modules.clients.service import ClientName, ClientService, build_client_service
from app.modules.scheduling.models import (
    Appointment,
    AppointmentStatus,
    AppointmentStatusHistory,
    AppointmentWhen,
    AvailabilityRule,
    BookingChannel,
    CancellationReason,
    TimeOff,
)
from app.modules.scheduling.repository import SchedulingRepository
from app.modules.scheduling.schemas import (
    BookAppointmentRequest,
    ChangeAppointmentStatusRequest,
    CreateAvailabilityRuleRequest,
    CreateStaffAppointmentRequest,
    CreateTimeOffRequest,
    RescheduleAppointmentRequest,
    UpdateAvailabilityRuleRequest,
    UpdateTimeOffRequest,
)
from app.modules.scheduling.slots import (
    Interval,
    SlotInputs,
    WeeklyRule,
    available_starts,
    is_available_start,
    local_period,
)
from app.modules.scheduling.state_machine import ensure_transition_allowed
from app.modules.users.actor import STAFF_ROLES, Actor
from app.modules.users.models import PsychologistProfile, UserRole
from app.modules.users.service import UserService, build_user_service

RULE_ENTITY = "availability_rule"
TIME_OFF_ENTITY = "time_off"
APPOINTMENT_ENTITY = "appointment"

ONE_DAY = timedelta(days=1)


class PsychologistIdRequiredError(AppError):
    status_code = 422
    code = ErrorCode.PSYCHOLOGIST_ID_REQUIRED
    message = "Choose the psychologist whose schedule to read."


class InvalidTimeRangeError(AppError):
    status_code = 422
    code = ErrorCode.INVALID_TIME_RANGE
    message = "The start must come before the end."


class DateRangeInvalidError(AppError):
    status_code = 422
    code = ErrorCode.DATE_RANGE_INVALID
    message = "The date range is reversed or too long."


class TimeOffConflictError(AppError):
    status_code = 409
    code = ErrorCode.TIME_OFF_CONFLICT
    message = "This period overlaps active appointments."


class EmailNotVerifiedError(AppError):
    status_code = 403
    code = ErrorCode.EMAIL_NOT_VERIFIED
    message = "Verify your email address first."


class SlotTakenError(AppError):
    status_code = 409
    code = ErrorCode.SLOT_TAKEN
    message = "This time has been taken."


class SlotUnavailableError(AppError):
    status_code = 409
    code = ErrorCode.SLOT_UNAVAILABLE
    message = "This time cannot be booked."


class CancellationWindowClosedError(AppError):
    status_code = 409
    code = ErrorCode.CANCELLATION_WINDOW_CLOSED
    message = "It is too late to change this appointment."


@dataclass(frozen=True)
class PublicProfile:
    """What anyone may see of a psychologist."""

    first_name: str
    last_name: str
    title: str | None
    slug: str
    bio: str | None
    timezone: str
    cancellation_window_hours: int
    services: Sequence[OfferedService]


@dataclass(frozen=True)
class PublicSlots:
    """Bookable times, in the timezone of the psychologist."""

    timezone: str
    slots: Sequence[Interval]


@dataclass(frozen=True)
class ClientAppointment:
    """An appointment as the client who holds it sees it."""

    id: uuid.UUID
    psychologist_name: str
    psychologist_slug: str
    service_name: str
    delivery_mode: DeliveryMode
    starts_at: datetime
    ends_at: datetime
    timezone: str
    status: AppointmentStatus
    price_amount: Decimal
    currency: str
    rescheduled_from_id: uuid.UUID | None
    cancelled_at: datetime | None
    cancellation_reason: CancellationReason | None
    created_at: datetime


@dataclass(frozen=True)
class StaffAppointment:
    """An appointment as the staff see it."""

    id: uuid.UUID
    psychologist_id: uuid.UUID
    client_id: uuid.UUID
    client_first_name: str
    client_last_name: str
    service_id: uuid.UUID
    service_name: str
    delivery_mode: DeliveryMode
    starts_at: datetime
    ends_at: datetime
    status: AppointmentStatus
    price_amount: Decimal
    currency: str
    booking_channel: BookingChannel
    booked_by_user_id: uuid.UUID
    rescheduled_from_id: uuid.UUID | None
    cancelled_at: datetime | None
    cancelled_by_user_id: uuid.UUID | None
    cancellation_reason: CancellationReason | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class TimeOffPeriod:
    """A period of time off. `reason` is private to its psychologist: None for anyone else."""

    id: uuid.UUID
    psychologist_id: uuid.UUID
    starts_at: datetime
    ends_at: datetime
    reason: str | None
    created_at: datetime
    updated_at: datetime


def _time_off_view(time_off: TimeOff, *, with_reason: bool) -> TimeOffPeriod:
    return TimeOffPeriod(
        id=time_off.id,
        psychologist_id=time_off.psychologist_id,
        starts_at=time_off.starts_at,
        ends_at=time_off.ends_at,
        reason=time_off.reason if with_reason else None,
        created_at=time_off.created_at,
        updated_at=time_off.updated_at,
    )


def _client_view(
    appointment: Appointment, profile: PsychologistProfile, service: ServiceSummary
) -> ClientAppointment:
    names = (profile.title, profile.first_name, profile.last_name)
    return ClientAppointment(
        id=appointment.id,
        psychologist_name=" ".join(name for name in names if name),
        psychologist_slug=profile.slug,
        service_name=service.name,
        delivery_mode=service.delivery_mode,
        starts_at=appointment.starts_at,
        ends_at=appointment.ends_at,
        timezone=profile.timezone,
        status=appointment.status,
        price_amount=appointment.price_amount,
        currency=appointment.currency,
        rescheduled_from_id=appointment.rescheduled_from_id,
        cancelled_at=appointment.cancelled_at,
        cancellation_reason=appointment.cancellation_reason,
        created_at=appointment.created_at,
    )


def _staff_view(
    appointment: Appointment, client: ClientName, service: ServiceSummary
) -> StaffAppointment:
    return StaffAppointment(
        id=appointment.id,
        psychologist_id=appointment.psychologist_id,
        client_id=appointment.client_id,
        client_first_name=client.first_name,
        client_last_name=client.last_name,
        service_id=appointment.service_id,
        service_name=service.name,
        delivery_mode=service.delivery_mode,
        starts_at=appointment.starts_at,
        ends_at=appointment.ends_at,
        status=appointment.status,
        price_amount=appointment.price_amount,
        currency=appointment.currency,
        booking_channel=appointment.booking_channel,
        booked_by_user_id=appointment.booked_by_user_id,
        rescheduled_from_id=appointment.rescheduled_from_id,
        cancelled_at=appointment.cancelled_at,
        cancelled_by_user_id=appointment.cancelled_by_user_id,
        cancellation_reason=appointment.cancellation_reason,
        created_at=appointment.created_at,
        updated_at=appointment.updated_at,
    )


def _booked_status(profile: PsychologistProfile) -> AppointmentStatus:
    if profile.auto_confirm_bookings:
        return AppointmentStatus.CONFIRMED
    return AppointmentStatus.REQUESTED


def _ensure_valid_rule(
    start_time: time, end_time: time, valid_from: date, valid_until: date | None
) -> None:
    if start_time >= end_time or (valid_until is not None and valid_until < valid_from):
        raise InvalidTimeRangeError


def _ensure_valid_period(starts_at: datetime, ends_at: datetime) -> None:
    if starts_at >= ends_at:
        raise InvalidTimeRangeError


class SchedulingService:
    def __init__(
        self,
        repository: SchedulingRepository,
        users: UserService,
        catalog: CatalogService,
        clients: ClientService,
        audit: AuditService,
        settings: Settings,
        clock: Clock,
    ) -> None:
        self._repository = repository
        self._users = users
        self._catalog = catalog
        self._clients = clients
        self._audit = audit
        self._settings = settings
        self._clock = clock

    async def list_rules(
        self, actor: Actor, psychologist_id: uuid.UUID | None, page: PageParams
    ) -> tuple[Sequence[AvailabilityRule], int]:
        owner = await self._schedule_owner(actor, psychologist_id)
        rules = await self._repository.list_rules(owner, page.limit, page.offset)
        return rules, await self._repository.count_rules(owner)

    async def create_rule(
        self, actor: Actor, request: CreateAvailabilityRuleRequest, *, ip_address: str | None
    ) -> AvailabilityRule:
        actor.require_role(UserRole.PSYCHOLOGIST)
        _ensure_valid_rule(
            request.start_time, request.end_time, request.valid_from, request.valid_until
        )
        rule = AvailabilityRule(psychologist_id=actor.user_id, **request.model_dump())
        await self._repository.add(rule)
        await self._record(
            AuditAction.AVAILABILITY_CREATED, actor, RULE_ENTITY, rule.id, ip_address
        )
        return rule

    async def update_rule(
        self,
        actor: Actor,
        rule_id: uuid.UUID,
        request: UpdateAvailabilityRuleRequest,
        *,
        ip_address: str | None,
    ) -> AvailabilityRule:
        actor.require_role(UserRole.PSYCHOLOGIST)
        rule = await self._repository.lock_rule(actor.user_id, rule_id)
        if rule is None:
            raise NotFoundError
        changes = changed_values(rule, request.model_dump(exclude_unset=True))
        if changes:
            _ensure_valid_rule(
                changes.get("start_time", rule.start_time),
                changes.get("end_time", rule.end_time),
                changes.get("valid_from", rule.valid_from),
                changes.get("valid_until", rule.valid_until),
            )
            await self._apply(rule, changes)
            await self._record(
                AuditAction.AVAILABILITY_UPDATED,
                actor,
                RULE_ENTITY,
                rule.id,
                ip_address,
                {"changed_fields": sorted(changes)},
            )
        return rule

    async def delete_rule(
        self, actor: Actor, rule_id: uuid.UUID, *, ip_address: str | None
    ) -> None:
        """Stop offering these hours. Appointments already made in them stay as they are."""
        actor.require_role(UserRole.PSYCHOLOGIST)
        if not await self._repository.delete_rule(actor.user_id, rule_id):
            raise NotFoundError
        await self._record(
            AuditAction.AVAILABILITY_DELETED, actor, RULE_ENTITY, rule_id, ip_address
        )

    async def list_time_off(
        self,
        actor: Actor,
        psychologist_id: uuid.UUID | None,
        page: PageParams,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> tuple[list[TimeOffPeriod], int]:
        """List the periods of a schedule, optionally only those overlapping start to end.

        An admin sees when a psychologist is away, but not why.
        """
        owner = await self._schedule_owner(actor, psychologist_id)
        if start is not None and end is not None and end < start:
            raise DateRangeInvalidError
        periods = await self._repository.list_time_off(owner, start, end, page.limit, page.offset)
        total = await self._repository.count_time_off(owner, start, end)
        with_reason = owner == actor.user_id
        return [_time_off_view(period, with_reason=with_reason) for period in periods], total

    async def create_time_off(
        self, actor: Actor, request: CreateTimeOffRequest, *, ip_address: str | None
    ) -> TimeOff:
        actor.require_role(UserRole.PSYCHOLOGIST)
        _ensure_valid_period(request.starts_at, request.ends_at)
        await self._lock_schedule(actor)
        await self._ensure_no_active_appointments(actor, request.starts_at, request.ends_at)
        time_off = TimeOff(psychologist_id=actor.user_id, **request.model_dump())
        await self._repository.add(time_off)
        await self._record(
            AuditAction.TIME_OFF_CREATED, actor, TIME_OFF_ENTITY, time_off.id, ip_address
        )
        return time_off

    async def update_time_off(
        self,
        actor: Actor,
        time_off_id: uuid.UUID,
        request: UpdateTimeOffRequest,
        *,
        ip_address: str | None,
    ) -> TimeOff:
        actor.require_role(UserRole.PSYCHOLOGIST)
        await self._lock_schedule(actor)
        time_off = await self._repository.lock_time_off(actor.user_id, time_off_id)
        if time_off is None:
            raise NotFoundError
        changes = changed_values(time_off, request.model_dump(exclude_unset=True))
        if changes:
            starts_at = changes.get("starts_at", time_off.starts_at)
            ends_at = changes.get("ends_at", time_off.ends_at)
            _ensure_valid_period(starts_at, ends_at)
            if changes.keys() & {"starts_at", "ends_at"}:
                await self._ensure_no_active_appointments(actor, starts_at, ends_at)
            await self._apply(time_off, changes)
            await self._record(
                AuditAction.TIME_OFF_UPDATED,
                actor,
                TIME_OFF_ENTITY,
                time_off.id,
                ip_address,
                {"changed_fields": sorted(changes)},
            )
        return time_off

    async def delete_time_off(
        self, actor: Actor, time_off_id: uuid.UUID, *, ip_address: str | None
    ) -> None:
        actor.require_role(UserRole.PSYCHOLOGIST)
        if not await self._repository.delete_time_off(actor.user_id, time_off_id):
            raise NotFoundError
        await self._record(
            AuditAction.TIME_OFF_DELETED, actor, TIME_OFF_ENTITY, time_off_id, ip_address
        )

    async def get_public_profile(self, slug: str) -> PublicProfile:
        profile = await self._require_public_profile(slug)
        return PublicProfile(
            first_name=profile.first_name,
            last_name=profile.last_name,
            title=profile.title,
            slug=profile.slug,
            bio=profile.bio,
            timezone=profile.timezone,
            cancellation_window_hours=profile.cancellation_window_hours,
            services=await self._catalog.list_offered_services(profile.user_id),
        )

    async def get_public_slots(
        self, slug: str, service_id: uuid.UUID, from_date: date, to_date: date
    ) -> PublicSlots:
        """Return the free slots of a service on a range of the psychologist's local dates."""
        # Both dates are included, so a difference of 30 days covers 31.
        if not 0 <= (to_date - from_date).days < self._settings.slot_max_range_days:
            raise DateRangeInvalidError
        profile = await self._require_public_profile(slug)
        service = await self._catalog.get_offered_service(profile.user_id, service_id)
        if service is None:
            raise NotFoundError
        duration = timedelta(minutes=service.duration_minutes)
        inputs = await self._slot_inputs(profile, duration, from_date, to_date)
        zone = inputs.zone
        return PublicSlots(
            timezone=profile.timezone,
            slots=[
                # The end is added in UTC: wall-clock arithmetic would be wrong across DST.
                Interval(starts_at.astimezone(zone), (starts_at + duration).astimezone(zone))
                for starts_at in available_starts(inputs)
            ],
        )

    async def book_appointment(
        self, actor: Actor, request: BookAppointmentRequest, *, ip_address: str | None
    ) -> ClientAppointment:
        """Book a free slot of a service for the client behind `actor`."""
        actor.require_role(UserRole.CLIENT)
        client = await self._verified_client(actor)
        public_profile = await self._require_public_profile(request.psychologist_slug)
        psychologist_id = public_profile.user_id
        service = await self._catalog.get_offered_service(psychologist_id, request.service_id)
        if service is None:
            raise NotFoundError

        profile = await self._lock_for_booking(public_profile)
        duration = timedelta(minutes=service.duration_minutes)
        await self._ensure_bookable(profile, request.starts_at, duration)
        appointment = Appointment(
            psychologist_id=psychologist_id,
            client_id=client.id,
            service_id=service.id,
            starts_at=request.starts_at,
            ends_at=request.starts_at + duration,
            status=_booked_status(profile),
            price_amount=service.price_amount,
            currency=service.currency,
            booking_channel=BookingChannel.ONLINE,
            booked_by_user_id=actor.user_id,
        )
        await self._place(appointment, actor)
        await self._record_creation(appointment, actor, ip_address)
        return (await self._client_views([appointment]))[0]

    async def list_own_appointments(
        self, actor: Actor, when: AppointmentWhen, page: PageParams
    ) -> tuple[list[ClientAppointment], int]:
        actor.require_role(UserRole.CLIENT)
        client = await self._clients.get_for_user(actor.user_id)
        if client is None:
            return [], 0
        now = self._clock()
        appointments = await self._repository.list_client_appointments(
            client.id, when, now, page.limit, page.offset
        )
        total = await self._repository.count_client_appointments(client.id, when, now)
        return await self._client_views(appointments), total

    async def cancel_own_appointment(
        self, actor: Actor, appointment_id: uuid.UUID, *, ip_address: str | None
    ) -> ClientAppointment:
        actor.require_role(UserRole.CLIENT)
        client = await self._clients.get_for_user(actor.user_id)
        if client is None:
            raise NotFoundError
        appointment, _ = await self._lock_for_client_change(client, appointment_id)
        await self._transition(
            appointment,
            AppointmentStatus.CANCELLED,
            actor,
            CancellationReason.CLIENT_REQUEST,
            ip_address=ip_address,
        )
        return (await self._client_views([appointment]))[0]

    async def reschedule_own_appointment(
        self,
        actor: Actor,
        appointment_id: uuid.UUID,
        request: RescheduleAppointmentRequest,
        *,
        ip_address: str | None,
    ) -> ClientAppointment:
        """Move the client's session to another free slot. Its service is not read again."""
        actor.require_role(UserRole.CLIENT)
        client = await self._verified_client(actor)
        old, unlocked_profile = await self._lock_for_client_change(client, appointment_id)
        # Unlike the service, which may be gone by now, the psychologist must still take bookings.
        if await self._users.get_active_profile_by_slug(unlocked_profile.slug) is None:
            raise SlotUnavailableError
        profile = await self._lock_for_booking(unlocked_profile)
        appointment = await self._reschedule(
            old,
            actor,
            request.starts_at,
            status=_booked_status(profile),
            channel=BookingChannel.ONLINE,
            ensure_free=partial(self._ensure_bookable, profile),
            ip_address=ip_address,
        )
        return (await self._client_views([appointment]))[0]

    async def list_appointments(
        self,
        actor: Actor,
        page: PageParams,
        *,
        psychologist_id: uuid.UUID | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        statuses: Collection[AppointmentStatus] = (),
    ) -> tuple[list[StaffAppointment], int]:
        """List appointments overlapping start to end, whatever became of the care relationship."""
        actor.require_role(*STAFF_ROLES)
        owner = await self._appointment_owner(actor, psychologist_id)
        if start is not None and end is not None and end < start:
            raise DateRangeInvalidError
        appointments = await self._repository.list_staff_appointments(
            owner, start, end, statuses, page.limit, page.offset
        )
        total = await self._repository.count_staff_appointments(owner, start, end, statuses)
        return await self._staff_views(appointments), total

    async def get_appointment(self, actor: Actor, appointment_id: uuid.UUID) -> StaffAppointment:
        actor.require_role(*STAFF_ROLES)
        owner = None if actor.role is UserRole.ADMIN else actor.user_id
        appointment = await self._repository.get_appointment(owner, appointment_id)
        if appointment is None:
            raise NotFoundError
        return (await self._staff_views([appointment]))[0]

    async def create_appointment(
        self, actor: Actor, request: CreateStaffAppointmentRequest, *, ip_address: str | None
    ) -> StaffAppointment:
        """Book on behalf of a client the psychologist has or had in care."""
        actor.require_role(UserRole.PSYCHOLOGIST)
        psychologist_id = actor.user_id
        if not await self._clients.has_care_relationship_row(psychologist_id, request.client_id):
            raise NotFoundError
        service = await self._catalog.get_offered_service(psychologist_id, request.service_id)
        if service is None:
            raise NotFoundError

        await self._users.lock_profile(psychologist_id, exclusive=False)
        duration = timedelta(minutes=service.duration_minutes)
        await self._ensure_staff_bookable(psychologist_id, request.starts_at, duration)
        appointment = Appointment(
            psychologist_id=psychologist_id,
            client_id=request.client_id,
            service_id=service.id,
            starts_at=request.starts_at,
            ends_at=request.starts_at + duration,
            status=AppointmentStatus.CONFIRMED,
            price_amount=service.price_amount,
            currency=service.currency,
            booking_channel=BookingChannel.STAFF,
            booked_by_user_id=actor.user_id,
        )
        await self._place(appointment, actor)
        await self._record_creation(appointment, actor, ip_address)
        return (await self._staff_views([appointment]))[0]

    async def change_status(
        self,
        actor: Actor,
        appointment_id: uuid.UUID,
        request: ChangeAppointmentStatusRequest,
        *,
        ip_address: str | None,
    ) -> StaffAppointment:
        """Apply a status. A late cancellation is recorded here: staff have no window."""
        actor.require_role(UserRole.PSYCHOLOGIST)
        appointment = await self._lock_for_staff_change(actor, appointment_id)
        reason = None
        if request.status is AppointmentStatus.CANCELLED:
            reason = request.cancellation_reason or CancellationReason.PSYCHOLOGIST_REQUEST
        await self._transition(appointment, request.status, actor, reason, ip_address=ip_address)
        return (await self._staff_views([appointment]))[0]

    async def reschedule_appointment(
        self,
        actor: Actor,
        appointment_id: uuid.UUID,
        request: RescheduleAppointmentRequest,
        *,
        ip_address: str | None,
    ) -> StaffAppointment:
        """Move a session under the rules of a staff booking."""
        actor.require_role(UserRole.PSYCHOLOGIST)
        old = await self._lock_for_staff_change(actor, appointment_id)
        await self._users.lock_profile(actor.user_id, exclusive=False)
        appointment = await self._reschedule(
            old,
            actor,
            request.starts_at,
            status=AppointmentStatus.CONFIRMED,
            channel=BookingChannel.STAFF,
            ensure_free=partial(self._ensure_staff_bookable, actor.user_id),
            ip_address=ip_address,
        )
        return (await self._staff_views([appointment]))[0]

    async def _slot_inputs(
        self, profile: PsychologistProfile, duration: timedelta, from_date: date, to_date: date
    ) -> SlotInputs:
        """Gather what slot calculation needs for a range of the psychologist's local dates."""
        zone = ZoneInfo(profile.timezone)
        buffer = timedelta(minutes=profile.buffer_minutes)
        now = self._clock()
        not_before = now + timedelta(hours=profile.min_booking_notice_hours)
        not_after = now + timedelta(days=profile.max_booking_advance_days)
        # Any date is a valid request, but one at the far end of the calendar would overflow
        # on the way to UTC, and no date outside the booking limits can hold a slot.
        first_date = max(from_date, not_before.astimezone(zone).date())
        last_date = min(to_date, not_after.astimezone(zone).date())
        inputs = SlotInputs(
            rules=(),
            zone=zone,
            duration=duration,
            buffer=buffer,
            granularity=timedelta(minutes=self._settings.slot_granularity_minutes),
            not_before=not_before,
            not_after=not_after,
            from_date=first_date,
            to_date=last_date,
            appointments=(),
            time_off=(),
        )
        if first_date > last_date:
            return inputs

        period = local_period(first_date, last_date, zone)
        start, end = period.starts_at - buffer, period.ends_at + buffer
        psychologist_id = profile.user_id
        rules = await self._repository.rules_in_effect(psychologist_id, first_date, last_date)
        appointments = await self._repository.appointment_ranges(psychologist_id, start, end)
        time_off = await self._repository.time_off_ranges(psychologist_id, start, end)
        return replace(
            inputs,
            rules=[
                WeeklyRule(
                    rule.weekday, rule.start_time, rule.end_time, rule.valid_from, rule.valid_until
                )
                for rule in rules
            ],
            appointments=[Interval(*times) for times in appointments],
            time_off=[Interval(*times) for times in time_off],
        )

    async def _ensure_bookable(
        self, profile: PsychologistProfile, starts_at: datetime, duration: timedelta
    ) -> None:
        """Apply every booking rule to a start. The caller must hold `_lock_for_booking`.

        Appointments come last, so that the loser of a race gets `SlotTakenError` whichever
        way it loses, and never the error for a time that was not bookable to begin with.
        """
        day = starts_at.astimezone(ZoneInfo(profile.timezone)).date()
        # A DST gap can push the start of a working window past midnight, onto the next date.
        inputs = await self._slot_inputs(profile, duration, day - ONE_DAY, day)
        if not is_available_start(replace(inputs, appointments=()), starts_at):
            raise SlotUnavailableError
        if not is_available_start(inputs, starts_at):
            raise SlotTakenError

    async def _ensure_staff_bookable(
        self, psychologist_id: uuid.UUID, starts_at: datetime, duration: timedelta
    ) -> None:
        """Staff may leave working hours and the buffer, but not book over time off.

        The caller must hold a lock on the profile. Overlap stays with the exclusion constraint.
        """
        if starts_at <= self._clock():
            raise SlotUnavailableError
        ends_at = starts_at + duration
        if await self._repository.time_off_ranges(psychologist_id, starts_at, ends_at):
            raise TimeOffConflictError(message="This time overlaps time off.")

    async def _verified_client(self, actor: Actor) -> Client:
        user = await self._users.get(actor.user_id)
        client = await self._clients.get_for_user(actor.user_id)
        if user is None or client is None:
            raise NotFoundError
        if user.email_verified_at is None:
            raise EmailNotVerifiedError
        return client

    async def _lock_for_booking(self, profile: PsychologistProfile) -> PsychologistProfile:
        """Lock a schedule exclusively for an online booking and return its current state.

        Online bookings of one psychologist run one after another. The exclusion constraint
        knows nothing about buffers, and a lock taken once never has to be upgraded, so
        two bookings cannot deadlock on the profile row (ADR-003).
        """
        return await self._users.lock_profile(profile.user_id, exclusive=True)

    async def _lock_for_client_change(
        self, client: Client, appointment_id: uuid.UUID
    ) -> tuple[Appointment, PsychologistProfile]:
        """Lock an appointment of a client who wants to cancel or move it, if still allowed."""
        appointment = await self._repository.lock_client_appointment(client.id, appointment_id)
        if appointment is None:
            raise NotFoundError
        now = self._clock()
        # A closed appointment gets this error even if its window has passed as well.
        ensure_transition_allowed(
            appointment.status,
            AppointmentStatus.CANCELLED,
            starts_at=appointment.starts_at,
            now=now,
        )
        psychologist_id = appointment.psychologist_id
        profile = (await self._users.get_profiles({psychologist_id}))[psychologist_id]
        window_hours = profile.cancellation_window_hours
        if now > appointment.starts_at - timedelta(hours=window_hours):
            raise CancellationWindowClosedError(details={"cancellation_window_hours": window_hours})
        return appointment, profile

    async def _lock_for_staff_change(self, actor: Actor, appointment_id: uuid.UUID) -> Appointment:
        appointment = await self._repository.lock_psychologist_appointment(
            actor.user_id, appointment_id
        )
        if appointment is None:
            raise NotFoundError
        return appointment

    async def _transition(
        self,
        appointment: Appointment,
        to_status: AppointmentStatus,
        actor: Actor,
        reason: CancellationReason | None,
        *,
        ip_address: str | None,
        action: AuditAction = AuditAction.APPOINTMENT_STATUS_CHANGED,
        extra_metadata: Mapping[str, str] | None = None,
    ) -> None:
        """Change a status, the only way to do so. The caller must have locked the row."""
        now = self._clock()
        from_status = appointment.status
        ensure_transition_allowed(from_status, to_status, starts_at=appointment.starts_at, now=now)
        appointment.status = to_status
        if to_status is AppointmentStatus.CANCELLED:
            appointment.cancelled_at = now
            appointment.cancelled_by_user_id = actor.user_id
            appointment.cancellation_reason = reason
        await self._repository.flush()
        await self._add_history(appointment, from_status, actor, reason, now)
        await self._record(
            action,
            actor,
            APPOINTMENT_ENTITY,
            appointment.id,
            ip_address,
            {
                "from_status": from_status,
                "to_status": to_status,
                "reason_category": reason,
                **(extra_metadata or {}),
            },
        )

    async def _reschedule(
        self,
        old: Appointment,
        actor: Actor,
        starts_at: datetime,
        *,
        status: AppointmentStatus,
        channel: BookingChannel,
        ensure_free: Callable[[datetime, timedelta], Awaitable[None]],
        ip_address: str | None,
    ) -> Appointment:
        """Replace a locked appointment by one at another time, keeping what was agreed.

        The cancellation comes first, so that the old appointment cannot block the time
        around itself. The caller's transaction undoes it if the new time turns out taken.
        """
        new_id = uuid.uuid4()
        await self._transition(
            old,
            AppointmentStatus.CANCELLED,
            actor,
            CancellationReason.RESCHEDULED,
            ip_address=ip_address,
            action=AuditAction.APPOINTMENT_RESCHEDULED,
            extra_metadata={"new_appointment_id": str(new_id)},
        )
        duration = old.ends_at - old.starts_at
        await ensure_free(starts_at, duration)
        appointment = Appointment(
            id=new_id,
            psychologist_id=old.psychologist_id,
            client_id=old.client_id,
            service_id=old.service_id,
            starts_at=starts_at,
            ends_at=starts_at + duration,
            status=status,
            price_amount=old.price_amount,
            currency=old.currency,
            booking_channel=channel,
            booked_by_user_id=actor.user_id,
            rescheduled_from_id=old.id,
        )
        await self._place(appointment, actor, CancellationReason.RESCHEDULED)
        return appointment

    async def _place(
        self,
        appointment: Appointment,
        actor: Actor,
        reason: CancellationReason | None = None,
    ) -> None:
        """Insert an appointment, leaving the last word on overlap to the exclusion constraint."""
        if not await self._repository.insert_appointment(appointment):
            raise SlotTakenError
        await self._clients.ensure_care_relationship(
            appointment.psychologist_id, appointment.client_id, actor.user_id
        )
        await self._add_history(appointment, None, actor, reason, self._clock())

    async def _add_history(
        self,
        appointment: Appointment,
        from_status: AppointmentStatus | None,
        actor: Actor,
        reason: CancellationReason | None,
        changed_at: datetime,
    ) -> None:
        await self._repository.add(
            AppointmentStatusHistory(
                appointment_id=appointment.id,
                from_status=from_status,
                to_status=appointment.status,
                changed_by_user_id=actor.user_id,
                changed_at=changed_at,
                reason_category=reason,
            )
        )

    async def _record_creation(
        self, appointment: Appointment, actor: Actor, ip_address: str | None
    ) -> None:
        await self._record(
            AuditAction.APPOINTMENT_CREATED,
            actor,
            APPOINTMENT_ENTITY,
            appointment.id,
            ip_address,
            {"status": appointment.status, "booking_channel": appointment.booking_channel},
        )

    async def _client_views(self, appointments: Sequence[Appointment]) -> list[ClientAppointment]:
        profiles = await self._users.get_profiles({row.psychologist_id for row in appointments})
        services = await self._catalog.get_service_summaries(
            {row.service_id for row in appointments}
        )
        return [
            _client_view(row, profiles[row.psychologist_id], services[row.service_id])
            for row in appointments
        ]

    async def _staff_views(self, appointments: Sequence[Appointment]) -> list[StaffAppointment]:
        names = await self._clients.get_display_names({row.client_id for row in appointments})
        services = await self._catalog.get_service_summaries(
            {row.service_id for row in appointments}
        )
        return [
            _staff_view(row, names[row.client_id], services[row.service_id]) for row in appointments
        ]

    async def _appointment_owner(
        self, actor: Actor, psychologist_id: uuid.UUID | None
    ) -> uuid.UUID | None:
        """Return the psychologist whose appointments to read, or None for all of them."""
        if actor.role is UserRole.ADMIN and psychologist_id is None:
            return None
        return await self._schedule_owner(actor, psychologist_id)

    async def _schedule_owner(self, actor: Actor, psychologist_id: uuid.UUID | None) -> uuid.UUID:
        """Return the psychologist whose schedule the actor may read under this filter."""
        actor.require_role(*STAFF_ROLES)
        if actor.role is not UserRole.ADMIN:
            if psychologist_id not in (None, actor.user_id):
                raise NotFoundError
            return actor.user_id
        if psychologist_id is None:
            raise PsychologistIdRequiredError
        if await self._users.get_profile(psychologist_id) is None:
            raise NotFoundError
        return psychologist_id

    async def _require_public_profile(self, slug: str) -> PsychologistProfile:
        profile = await self._users.get_active_profile_by_slug(slug)
        if profile is None:
            raise NotFoundError
        return profile

    async def _lock_schedule(self, actor: Actor) -> None:
        """Wait for bookings in flight, and make later ones wait for this change to time off."""
        await self._users.lock_profile(actor.user_id, exclusive=True)

    async def _ensure_no_active_appointments(
        self, actor: Actor, starts_at: datetime, ends_at: datetime
    ) -> None:
        """Reject a period over appointments. The caller must hold `_lock_schedule`."""
        count = await self._repository.count_active_appointments(actor.user_id, starts_at, ends_at)
        if count:
            raise TimeOffConflictError(details={"appointment_count": count})

    async def _apply(self, row: AvailabilityRule | TimeOff, changes: dict[str, Any]) -> None:
        for name, value in changes.items():
            setattr(row, name, value)
        await self._repository.flush()

    async def _record(
        self,
        action: AuditAction,
        actor: Actor,
        entity_type: str,
        entity_id: uuid.UUID,
        ip_address: str | None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        await self._audit.record(
            action,
            actor_user_id=actor.user_id,
            entity_type=entity_type,
            entity_id=entity_id,
            ip_address=ip_address,
            metadata=metadata,
        )


def build_scheduling_service(
    session: AsyncSession, settings: Settings, clock: Clock
) -> SchedulingService:
    return SchedulingService(
        SchedulingRepository(session),
        build_user_service(session, clock),
        build_catalog_service(session, clock),
        build_client_service(session, settings, clock),
        build_audit_service(session),
        settings,
        clock,
    )


class AppointmentLookupService:
    """Answers other modules about appointments. Those modules authorize their own callers."""

    def __init__(self, repository: SchedulingRepository, clock: Clock) -> None:
        self._repository = repository
        self._clock = clock

    async def has_upcoming_appointments(
        self, psychologist_id: uuid.UUID, client_id: uuid.UUID
    ) -> bool:
        """Tell whether an active appointment between the two has not finished yet."""
        return await self._repository.has_upcoming_appointments(
            psychologist_id, client_id, self._clock()
        )


def build_appointment_lookup(session: AsyncSession, clock: Clock) -> AppointmentLookupService:
    return AppointmentLookupService(SchedulingRepository(session), clock)
