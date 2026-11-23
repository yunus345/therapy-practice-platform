"""Builds the synthetic demo data in memory, as rows ready to be added to a session.

All randomness comes from one generator seeded with `SEED`, so the same day and the same
Faker version give the same data, identifiers included. How each session turns out is
decided in `behavior`; this module only places sessions on the calendar and records what
happened the way the application would have.
"""

import random
import uuid
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from faker import Faker

from app.core.models import Base
from app.modules.catalog.models import DeliveryMode, PsychologistService, Service
from app.modules.clients.models import Client, PsychologistClient
from app.modules.consent.models import Consent, ConsentType
from app.modules.scheduling.models import (
    Appointment,
    AppointmentStatus,
    AppointmentStatusHistory,
    AvailabilityRule,
    BookingChannel,
    CancellationReason,
    TimeOff,
)
from app.modules.users.models import PsychologistProfile, User, UserRole
from app.seed import behavior
from app.seed.behavior import EarlyCancellationKind, Outcome, SessionContext
from app.seed.fixtures import (
    ADMIN_EMAIL,
    ADOLESCENT,
    EMAIL_DOMAIN,
    INTAKE,
    PSYCHOLOGISTS,
    SERVICES,
    PsychologistSpec,
    ServiceSpec,
)

SEED = 20261102
TIMEZONE = ZoneInfo("Europe/Istanbul")
KVKK_DISCLOSURE_VERSION = "1.0"

PAST_WEEKS = 26
UPCOMING_WEEKS = 4
MIN_BOOKING_NOTICE_HOURS = 24
MAX_BOOKING_ADVANCE_DAYS = 60

# Share of clients who book online when they have an account; the rest call the office.
ONLINE_BOOKING_SHARE = 0.85
# Share of sessions booked right at the end of the previous one.
BOOKED_AT_LAST_SESSION_SHARE = 0.65
INTAKE_SHARE = 0.7
WEEKLY_SHARE = 0.75
SKIPPED_WEEK_SHARE = 0.08
EMAIL_SHARE = 0.85
BIRTH_DATE_SHARE = 0.7
# Clients already in therapy when the seeded period begins.
ONGOING_CLIENT_SHARE = 0.4
ENDED_CARE_SHARE = 0.5
# Planned number of sessions: (weight, fewest, most).
THERAPY_LENGTHS = ((25, 6, 10), (45, 10, 24), (30, 24, 40))
RESCHEDULE_SEARCH_DAYS = 10

TURKISH_TO_ASCII = str.maketrans("çğıöşüÇĞİÖŞÜ", "cgiosuCGIOSU")


def _local(day: date, clock_time: time) -> datetime:
    return datetime.combine(day, clock_time, TIMEZONE)


def _email_name(text: str) -> str:
    return "".join(text.translate(TURKISH_TO_ASCII).lower().split())


@dataclass
class ClientPlan:
    """A client of one psychologist, and what has happened to them so far."""

    client: Client
    user: User | None
    psychologist: PsychologistSpec
    psychologist_id: uuid.UUID
    main_service: str
    starts_with_intake: bool
    weekday: int
    hour: int
    dates: list[date]
    booked: int = 0
    completed: int = 0
    no_shows: int = 0
    late_cancellations: int = 0
    dropped_out: bool = False
    appointments: list[Appointment] = field(default_factory=list)


@dataclass
class Calendar:
    """One psychologist's working hours, time off and booked sessions."""

    spec: PsychologistSpec
    time_off: list[tuple[datetime, datetime]] = field(default_factory=list)
    busy: dict[date, list[tuple[datetime, datetime]]] = field(
        default_factory=lambda: defaultdict(list)
    )

    def fits(self, start: datetime, duration: timedelta) -> bool:
        hours = self.spec.hours.get(start.weekday())
        end = start + duration
        if hours is None or start.time() < hours[0]:
            return False
        if end.date() != start.date() or end.time() > hours[1]:
            return False
        if any(start < off_end and off_start < end for off_start, off_end in self.time_off):
            return False
        buffer = timedelta(minutes=self.spec.buffer_minutes)
        return not any(
            start - buffer < taken_end and taken_start < end + buffer
            for taken_start, taken_end in self.busy[start.date()]
        )

    def place(self, day: date, hour: int, duration: timedelta) -> datetime | None:
        """Find a start on the hour, as close to the preferred hour as the day allows."""
        hours = self.spec.hours.get(day.weekday())
        if hours is None:
            return None
        candidates = sorted(
            range(hours[0].hour, hours[1].hour), key=lambda each: (abs(each - hour), each)
        )
        for candidate in candidates:
            start = _local(day, time(candidate))
            if self.fits(start, duration):
                return start
        return None

    def book(self, start: datetime, end: datetime) -> None:
        self.busy[start.astimezone(TIMEZONE).date()].append((start, end))


@dataclass
class DemoData:
    """Rows in an order that satisfies every foreign key."""

    users: list[User] = field(default_factory=list)
    profiles: list[PsychologistProfile] = field(default_factory=list)
    services: list[Service] = field(default_factory=list)
    assignments: list[PsychologistService] = field(default_factory=list)
    rules: list[AvailabilityRule] = field(default_factory=list)
    time_off: list[TimeOff] = field(default_factory=list)
    clients: list[Client] = field(default_factory=list)
    relationships: list[PsychologistClient] = field(default_factory=list)
    consents: list[Consent] = field(default_factory=list)
    appointments: list[Appointment] = field(default_factory=list)
    history: list[AppointmentStatusHistory] = field(default_factory=list)

    def groups(self) -> Sequence[Sequence[Base]]:
        return (
            self.users,
            self.profiles,
            self.services,
            self.assignments,
            self.rules,
            self.time_off,
            self.clients,
            self.relationships,
            self.consents,
            self.appointments,
            self.history,
        )

    def status_counts(self) -> Counter[AppointmentStatus]:
        return Counter(appointment.status for appointment in self.appointments)


class DemoBuilder:
    def __init__(self, today: date, password_hash: str) -> None:
        # Reproducible demo data, never used for anything secret.
        self._rng = random.Random(SEED)  # noqa: S311
        self._faker = Faker("tr_TR")
        self._faker.seed_instance(SEED)
        self._password_hash = password_hash
        self._today = today
        # Sessions from today on are upcoming; everything before has an outcome.
        self._now = _local(today, time()).astimezone(UTC)
        self._first_day = today - timedelta(weeks=PAST_WEEKS)
        self._last_day = today + timedelta(weeks=UPCOMING_WEEKS)
        self._setup_at = _local(self._first_day - timedelta(days=30), time(9)).astimezone(UTC)
        self._data = DemoData()
        self._services: dict[str, tuple[Service, ServiceSpec]] = {}
        self._emails: set[str] = set()

    def build(self) -> DemoData:
        self._add_user(ADMIN_EMAIL, UserRole.ADMIN, self._setup_at)
        for service in SERVICES:
            self._add_service(service)
        plans: list[ClientPlan] = []
        calendars: dict[uuid.UUID, Calendar] = {}
        for spec in PSYCHOLOGISTS:
            psychologist = self._add_psychologist(spec)
            calendars[psychologist.id] = self._add_schedule(spec, psychologist.id)
            plans.extend(self._add_clients(spec, psychologist.id))
        self._book_sessions(plans, calendars)
        for plan in plans:
            self._finish_client(plan)
        return self._data

    def _id(self) -> uuid.UUID:
        return uuid.UUID(int=self._rng.getrandbits(128), version=4)

    def _add_user(self, email: str, role: UserRole, created_at: datetime) -> User:
        user = User(
            id=self._id(),
            email=email,
            password_hash=self._password_hash,
            role=role,
            is_active=True,
            email_verified_at=created_at + timedelta(minutes=10),
            created_at=created_at,
            updated_at=created_at,
        )
        self._data.users.append(user)
        return user

    def _add_service(self, spec: ServiceSpec) -> None:
        service = Service(
            id=self._id(),
            name=spec.name,
            description=spec.description,
            duration_minutes=spec.duration_minutes,
            price_amount=spec.price_amount,
            currency="TRY",
            delivery_mode=spec.delivery_mode,
            is_active=True,
            created_at=self._setup_at,
            updated_at=self._setup_at,
        )
        self._data.services.append(service)
        self._services[spec.key] = (service, spec)

    def _add_psychologist(self, spec: PsychologistSpec) -> User:
        created_at = self._setup_at + timedelta(hours=1)
        user = self._add_user(spec.email, UserRole.PSYCHOLOGIST, created_at)
        self._emails.add(spec.email)
        self._data.profiles.append(
            PsychologistProfile(
                user_id=user.id,
                first_name=spec.first_name,
                last_name=spec.last_name,
                title=spec.title,
                slug=spec.slug,
                bio=spec.bio,
                phone=spec.phone,
                timezone=TIMEZONE.key,
                min_booking_notice_hours=MIN_BOOKING_NOTICE_HOURS,
                max_booking_advance_days=MAX_BOOKING_ADVANCE_DAYS,
                cancellation_window_hours=spec.cancellation_window_hours,
                auto_confirm_bookings=spec.auto_confirm_bookings,
                buffer_minutes=spec.buffer_minutes,
                created_at=created_at,
                updated_at=created_at,
            )
        )
        for key in (INTAKE, *spec.main_services):
            self._data.assignments.append(
                PsychologistService(
                    id=self._id(),
                    psychologist_id=user.id,
                    service_id=self._services[key][0].id,
                    custom_price_amount=spec.custom_prices.get(key),
                    created_at=created_at,
                    updated_at=created_at,
                )
            )
        return user

    def _add_schedule(self, spec: PsychologistSpec, psychologist_id: uuid.UUID) -> Calendar:
        created_at = self._setup_at + timedelta(hours=2)
        for weekday, (start_time, end_time) in spec.hours.items():
            self._data.rules.append(
                AvailabilityRule(
                    id=self._id(),
                    psychologist_id=psychologist_id,
                    weekday=weekday,
                    start_time=start_time,
                    end_time=end_time,
                    valid_from=self._first_day - timedelta(days=7),
                    valid_until=None,
                    created_at=created_at,
                    updated_at=created_at,
                )
            )
        calendar = Calendar(spec)
        monday = self._today - timedelta(days=self._today.weekday())
        for period in spec.time_off:
            first = monday + timedelta(weeks=period.weeks_from_today, days=period.weekday)
            starts_at = _local(first, time()).astimezone(UTC)
            ends_at = _local(first + timedelta(days=period.days), time()).astimezone(UTC)
            noted_at = min(starts_at - timedelta(days=21), self._now - timedelta(days=1))
            self._data.time_off.append(
                TimeOff(
                    id=self._id(),
                    psychologist_id=psychologist_id,
                    starts_at=starts_at,
                    ends_at=ends_at,
                    reason=period.reason,
                    created_at=noted_at,
                    updated_at=noted_at,
                )
            )
            calendar.time_off.append((starts_at, ends_at))
        return calendar

    def _add_clients(self, spec: PsychologistSpec, psychologist_id: uuid.UUID) -> list[ClientPlan]:
        with_account = set(self._rng.sample(range(spec.client_count), spec.account_count))
        main_keys = list(spec.main_services)
        weights = list(spec.main_services.values())
        plans = []
        for index in range(spec.client_count):
            main_service = self._rng.choices(main_keys, weights)[0]
            plan = self._plan_client(
                spec, psychologist_id, main_service, has_account=index in with_account
            )
            plans.append(plan)
        return plans

    def _plan_client(
        self,
        spec: PsychologistSpec,
        psychologist_id: uuid.UUID,
        main_service: str,
        *,
        has_account: bool,
    ) -> ClientPlan:
        female = self._rng.random() < 0.6
        first_name = self._faker.first_name_female() if female else self._faker.first_name_male()
        last_name = self._faker.last_name()
        email = (
            self._unique_email(first_name, last_name)
            if has_account or self._rng.random() < EMAIL_SHARE
            else None
        )
        client = Client(
            id=self._id(),
            first_name=first_name,
            last_name=last_name,
            email=email,
            phone=self._phone(),
            birth_date=self._birth_date(adolescent=main_service == ADOLESCENT),
            deleted_at=None,
        )
        user = None
        if email is not None and has_account:
            user = User(
                id=self._id(),
                email=email,
                password_hash=self._password_hash,
                role=UserRole.CLIENT,
                is_active=True,
            )
            client.user_id = user.id
        weekday = self._rng.choice(list(spec.hours))
        opens, closes = spec.hours[weekday]
        duration = self._services[main_service][1].duration_minutes
        latest = closes.hour - (duration + 59) // 60
        return ClientPlan(
            client=client,
            user=user,
            psychologist=spec,
            psychologist_id=psychologist_id,
            main_service=main_service,
            starts_with_intake=self._rng.random() < INTAKE_SHARE,
            weekday=weekday,
            hour=self._rng.randint(opens.hour, max(opens.hour, latest)),
            dates=self._session_dates(weekday),
        )

    def _session_dates(self, weekday: int) -> list[date]:
        if self._rng.random() < ONGOING_CLIENT_SHARE:
            start = self._first_day
        else:
            span = (self._last_day - self._first_day).days
            start = self._first_day + timedelta(days=self._rng.randrange(span))
        start += timedelta(days=(weekday - start.weekday()) % 7)
        lengths = [(fewest, most) for _, fewest, most in THERAPY_LENGTHS]
        weights = [weight for weight, _, _ in THERAPY_LENGTHS]
        fewest, most = self._rng.choices(lengths, weights)[0]
        planned = self._rng.randint(fewest, most)
        step = timedelta(weeks=1 if self._rng.random() < WEEKLY_SHARE else 2)
        dates: list[date] = []
        day = start
        while len(dates) < planned and day < self._last_day:
            if self._rng.random() >= SKIPPED_WEEK_SHARE:
                dates.append(day)
            day += step
        return dates

    def _unique_email(self, first_name: str, last_name: str) -> str:
        base = f"{_email_name(first_name)}.{_email_name(last_name)}"
        email = f"{base}@{EMAIL_DOMAIN}"
        number = 2
        while email in self._emails:
            email = f"{base}{number}@{EMAIL_DOMAIN}"
            number += 1
        self._emails.add(email)
        return email

    def _phone(self) -> str:
        digits = [self._rng.randint(0, 9) for _ in range(7)]
        operator = f"5{self._rng.choice('345')}{self._rng.randint(0, 9)}"
        return (
            f"+90 {operator} {digits[0]}{digits[1]}{digits[2]} "
            f"{digits[3]}{digits[4]} {digits[5]}{digits[6]}"
        )

    def _birth_date(self, *, adolescent: bool) -> date | None:
        if adolescent:
            age = self._rng.randint(13, 17)
        elif self._rng.random() < BIRTH_DATE_SHARE:
            age = self._rng.randint(19, 65)
        else:
            return None
        return self._today - timedelta(days=age * 365 + self._rng.randrange(365))

    def _book_sessions(self, plans: list[ClientPlan], calendars: dict[uuid.UUID, Calendar]) -> None:
        wishes = sorted(
            ((day, plan.hour, index) for index, plan in enumerate(plans) for day in plan.dates),
        )
        for day, _, index in wishes:
            plan = plans[index]
            if plan.dropped_out:
                continue
            calendar = calendars[plan.psychologist_id]
            key = INTAKE if plan.starts_with_intake and plan.booked == 0 else plan.main_service
            service, spec = self._services[key]
            duration = timedelta(minutes=spec.duration_minutes)
            start = calendar.place(day, plan.hour, duration)
            if start is None:
                continue
            booked_at = self._booking_time(plan, start.astimezone(UTC))
            self._book(plan, calendar, service, spec, start, booked_at, original=None)

    def _booking_time(self, plan: ClientPlan, starts_at: datetime) -> datetime:
        previous = plan.appointments[-1] if plan.appointments else None
        if (
            previous is not None
            and previous.ends_at < starts_at
            and self._rng.random() < BOOKED_AT_LAST_SESSION_SHARE
        ):
            booked_at = previous.ends_at + timedelta(minutes=self._rng.randint(2, 20))
        else:
            days = self._rng.randint(2, 21) if plan.booked == 0 else self._rng.randint(1, 14)
            hours = self._rng.randint(0, 10)
            booked_at = starts_at - timedelta(days=days, hours=hours)
        # Nothing can have been booked after the moment the seed stands for.
        return min(booked_at, self._now - timedelta(minutes=self._rng.randint(30, 600)))

    def _book(
        self,
        plan: ClientPlan,
        calendar: Calendar,
        service: Service,
        spec: ServiceSpec,
        start: datetime,
        booked_at: datetime,
        *,
        original: Appointment | None,
        booked_by_client: bool | None = None,
    ) -> None:
        psychologist = plan.psychologist
        starts_at = start.astimezone(UTC)
        ends_at = starts_at + timedelta(minutes=spec.duration_minutes)
        lead_time = starts_at - booked_at
        if booked_by_client is None:
            booked_by_client = plan.user is not None and self._rng.random() < ONLINE_BOOKING_SHARE
        online = (
            booked_by_client
            and plan.user is not None
            and lead_time >= timedelta(hours=MIN_BOOKING_NOTICE_HOURS)
        )
        price = psychologist.custom_prices.get(spec.key, spec.price_amount)
        appointment = Appointment(
            id=self._id(),
            psychologist_id=plan.psychologist_id,
            client_id=plan.client.id,
            service_id=service.id,
            starts_at=starts_at,
            ends_at=ends_at,
            status=(
                AppointmentStatus.REQUESTED
                if online and not psychologist.auto_confirm_bookings
                else AppointmentStatus.CONFIRMED
            ),
            price_amount=price,
            currency="TRY",
            booking_channel=BookingChannel.ONLINE if online else BookingChannel.STAFF,
            booked_by_user_id=plan.user.id if online and plan.user else plan.psychologist_id,
            rescheduled_from_id=original.id if original else None,
            cancelled_at=None,
            cancelled_by_user_id=None,
            cancellation_reason=None,
            created_at=booked_at,
            updated_at=booked_at,
        )
        self._data.appointments.append(appointment)
        calendar.book(starts_at, ends_at)
        plan.booked += 1
        plan.appointments.append(appointment)
        self._record(
            appointment,
            None,
            appointment.booked_by_user_id,
            booked_at,
            CancellationReason.RESCHEDULED if original else None,
        )

        confirm_at = None
        if appointment.status is AppointmentStatus.REQUESTED:
            confirm_at = min(
                booked_at + timedelta(hours=self._rng.randint(1, 20)),
                starts_at - timedelta(hours=1),
            )
        context = SessionContext(
            lead_time=lead_time,
            cancellation_window=timedelta(hours=psychologist.cancellation_window_hours),
            weekday=start.weekday(),
            local_hour=start.hour,
            is_first_session=plan.completed == 0,
            prior_no_shows=plan.no_shows,
            prior_late_cancellations=plan.late_cancellations,
            booked_online=online,
            online_session=spec.delivery_mode is DeliveryMode.ONLINE,
        )
        outcome = behavior.decide_outcome(context, self._rng)
        if outcome is Outcome.EARLY_CANCELLATION:
            latest = starts_at - context.cancellation_window - timedelta(hours=1)
            cancelled_at = self._between(booked_at + timedelta(hours=1), latest)
            if cancelled_at < self._now:
                self._confirm_before(appointment, confirm_at, cancelled_at)
                self._cancel_early(plan, calendar, appointment, spec, cancelled_at)
                return
        elif outcome is Outcome.LATE_CANCELLATION and starts_at < self._now:
            earliest = max(booked_at, starts_at - context.cancellation_window)
            cancelled_at = self._between(
                earliest + timedelta(minutes=30), starts_at - timedelta(minutes=30)
            )
            self._confirm_before(appointment, confirm_at, cancelled_at)
            self._cancel(
                appointment,
                cancelled_at,
                plan.psychologist_id,
                CancellationReason.LATE_CANCELLATION,
            )
            plan.late_cancellations += 1
            return

        if starts_at >= self._now:
            self._confirm_before(appointment, confirm_at, self._now)
            return
        self._confirm_before(appointment, confirm_at, starts_at)
        marked_at = ends_at + timedelta(minutes=self._rng.randint(5, 180))
        if outcome is Outcome.NO_SHOW:
            self._change(appointment, AppointmentStatus.NO_SHOW, plan.psychologist_id, marked_at)
            plan.no_shows += 1
            plan.dropped_out = behavior.drops_out(self._rng)
        else:
            self._change(appointment, AppointmentStatus.COMPLETED, plan.psychologist_id, marked_at)
            plan.completed += 1

    def _cancel_early(
        self,
        plan: ClientPlan,
        calendar: Calendar,
        appointment: Appointment,
        spec: ServiceSpec,
        cancelled_at: datetime,
    ) -> None:
        kind = behavior.decide_early_cancellation(
            self._rng, can_reschedule=appointment.rescheduled_from_id is None
        )
        if kind is EarlyCancellationKind.PSYCHOLOGIST:
            self._cancel(
                appointment,
                cancelled_at,
                plan.psychologist_id,
                CancellationReason.PSYCHOLOGIST_REQUEST,
            )
            return
        by_client = plan.user is not None and self._rng.random() < ONLINE_BOOKING_SHARE
        actor = plan.user.id if by_client and plan.user else plan.psychologist_id
        new_start = None
        if kind is EarlyCancellationKind.RESCHEDULE:
            new_start = self._reschedule_target(calendar, appointment, spec, cancelled_at)
        if new_start is None:
            self._cancel(appointment, cancelled_at, actor, CancellationReason.CLIENT_REQUEST)
            return
        self._cancel(appointment, cancelled_at, actor, CancellationReason.RESCHEDULED)
        service = self._services[spec.key][0]
        self._book(
            plan,
            calendar,
            service,
            spec,
            new_start,
            cancelled_at,
            original=appointment,
            booked_by_client=by_client,
        )

    def _reschedule_target(
        self,
        calendar: Calendar,
        appointment: Appointment,
        spec: ServiceSpec,
        cancelled_at: datetime,
    ) -> datetime | None:
        local_start = appointment.starts_at.astimezone(TIMEZONE)
        earliest = cancelled_at + timedelta(hours=MIN_BOOKING_NOTICE_HOURS)
        duration = timedelta(minutes=spec.duration_minutes)
        for offset in range(1, RESCHEDULE_SEARCH_DAYS + 1):
            day = local_start.date() + timedelta(days=offset)
            if day >= self._last_day:
                return None
            start = calendar.place(day, local_start.hour, duration)
            if start is not None and start >= earliest:
                return start
        return None

    def _between(self, earliest: datetime, latest: datetime) -> datetime:
        if latest <= earliest:
            return earliest
        seconds = int((latest - earliest).total_seconds())
        return earliest + timedelta(seconds=self._rng.randrange(seconds))

    def _confirm_before(
        self, appointment: Appointment, confirm_at: datetime | None, deadline: datetime
    ) -> None:
        """Confirm a requested appointment if the psychologist got to it before `deadline`."""
        if confirm_at is not None and confirm_at < deadline:
            self._change(
                appointment, AppointmentStatus.CONFIRMED, appointment.psychologist_id, confirm_at
            )

    def _cancel(
        self,
        appointment: Appointment,
        cancelled_at: datetime,
        actor_id: uuid.UUID,
        reason: CancellationReason,
    ) -> None:
        appointment.cancelled_at = cancelled_at
        appointment.cancelled_by_user_id = actor_id
        appointment.cancellation_reason = reason
        self._change(appointment, AppointmentStatus.CANCELLED, actor_id, cancelled_at, reason)

    def _change(
        self,
        appointment: Appointment,
        status: AppointmentStatus,
        actor_id: uuid.UUID,
        changed_at: datetime,
        reason: CancellationReason | None = None,
    ) -> None:
        previous = appointment.status
        appointment.status = status
        appointment.updated_at = changed_at
        self._record(appointment, previous, actor_id, changed_at, reason)

    def _record(
        self,
        appointment: Appointment,
        from_status: AppointmentStatus | None,
        actor_id: uuid.UUID,
        changed_at: datetime,
        reason: CancellationReason | None,
    ) -> None:
        self._data.history.append(
            AppointmentStatusHistory(
                id=self._id(),
                appointment_id=appointment.id,
                from_status=from_status,
                to_status=appointment.status,
                changed_by_user_id=actor_id,
                changed_at=changed_at,
                reason_category=reason,
            )
        )

    def _finish_client(self, plan: ClientPlan) -> None:
        """Date the client's records from their first booking, and end finished care."""
        client = plan.client
        if plan.appointments:
            first_booking = min(appointment.created_at for appointment in plan.appointments)
            created_at = first_booking - timedelta(minutes=self._rng.randint(5, 600))
        else:
            created_at = self._now - timedelta(days=self._rng.randint(1, 30))
        client.created_at = created_at
        client.updated_at = created_at
        if plan.user is not None:
            client.created_by_user_id = plan.user.id
            plan.user.created_at = created_at
            plan.user.updated_at = created_at
            plan.user.email_verified_at = created_at + timedelta(minutes=10)
            self._data.users.append(plan.user)
        else:
            client.created_by_user_id = plan.psychologist_id
        self._data.clients.append(client)
        self._data.consents.append(
            Consent(
                id=self._id(),
                client_id=client.id,
                consent_type=ConsentType.KVKK_DISCLOSURE,
                version=KVKK_DISCLOSURE_VERSION,
                granted_at=created_at,
                revoked_at=None,
                ip_address=None,
                recorded_by_user_id=None if plan.user else plan.psychologist_id,
                created_at=created_at,
                updated_at=created_at,
            )
        )
        # A client with an account comes into care with the first booking.
        if not plan.appointments and plan.user is not None:
            return
        ended_at = self._care_ended_at(plan)
        self._data.relationships.append(
            PsychologistClient(
                id=self._id(),
                psychologist_id=plan.psychologist_id,
                client_id=client.id,
                ended_at=ended_at,
                created_at=created_at,
                updated_at=ended_at or created_at,
            )
        )

    def _care_ended_at(self, plan: ClientPlan) -> datetime | None:
        if not plan.appointments:
            return None
        last = max(appointment.ends_at for appointment in plan.appointments)
        ended_at = last + timedelta(days=14)
        if ended_at >= self._now - timedelta(days=30) or self._rng.random() >= ENDED_CARE_SHARE:
            return None
        return ended_at


def build_demo_data(today: date, password_hash: str) -> DemoData:
    return DemoBuilder(today, password_hash).build()
