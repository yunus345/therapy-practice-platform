import uuid
from typing import Any

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import utcnow
from app.core.errors import NotFoundError
from app.modules.audit.service import AuditAction
from app.modules.users.actor import Actor, ForbiddenError
from app.modules.users.models import PsychologistProfile, User, UserRole
from app.modules.users.profile_service import ProfileService, build_profile_service
from app.modules.users.schemas import UpdateProfileRequest
from tests.conftest import ClientFactory, UserFactory, audit_entries, log_in

CHANGES: dict[str, Any] = {
    "first_name": "Ada",
    "last_name": "Lovelace",
    "title": "Clinical Psychologist",
    "slug": "ada-lovelace",
    "bio": "I work with adults.",
    "phone": "+90 532 123 45 67",
    "timezone": "Europe/London",
    "min_booking_notice_hours": 12,
    "max_booking_advance_days": 30,
    "cancellation_window_hours": 48,
    "auto_confirm_bookings": True,
    "buffer_minutes": 10,
}
NOT_NULLABLE = [
    "first_name",
    "last_name",
    "slug",
    "timezone",
    "min_booking_notice_hours",
    "max_booking_advance_days",
    "cancellation_window_hours",
    "auto_confirm_bookings",
    "buffer_minutes",
]


async def load_profile(session: AsyncSession, user: User) -> PsychologistProfile:
    profile = await session.get(PsychologistProfile, user.id, populate_existing=True)
    assert profile is not None
    return profile


async def test_psychologist_reads_their_own_profile(
    api: httpx.AsyncClient, psychologist: User
) -> None:
    response = await api.get("/staff/profile")

    assert response.status_code == 200
    assert response.json() == {
        "user_id": str(psychologist.id),
        "first_name": "A",
        "last_name": "B",
        "title": None,
        "slug": response.json()["slug"],
        "bio": None,
        "phone": None,
        "timezone": "Europe/Istanbul",
        "min_booking_notice_hours": 24,
        "max_booking_advance_days": 60,
        "cancellation_window_hours": 24,
        "auto_confirm_bookings": False,
        "buffer_minutes": 0,
    }


async def test_psychologist_updates_every_editable_field(
    api: httpx.AsyncClient, psychologist: User, db_session: AsyncSession
) -> None:
    response = await api.patch("/staff/profile", json=CHANGES)

    assert response.status_code == 200
    assert response.json() == CHANGES | {"user_id": str(psychologist.id)}
    profile = await load_profile(db_session, psychologist)
    assert {name: getattr(profile, name) for name in CHANGES} == CHANGES


async def test_patch_leaves_absent_fields_alone(api: httpx.AsyncClient, psychologist: User) -> None:
    await api.patch("/staff/profile", json=CHANGES)

    response = await api.patch("/staff/profile", json={"buffer_minutes": 20})

    assert response.json() == CHANGES | {
        "user_id": str(psychologist.id),
        "buffer_minutes": 20,
    }


async def test_patch_changes_only_the_callers_profile(
    api: httpx.AsyncClient,
    psychologist: User,
    make_user: UserFactory,
    db_session: AsyncSession,
) -> None:
    colleague = await make_user(UserRole.PSYCHOLOGIST)

    await api.patch("/staff/profile", json={"first_name": "New"})

    assert (await load_profile(db_session, colleague)).first_name == "A"


async def test_patch_ignores_fields_that_are_not_editable(
    api: httpx.AsyncClient,
    psychologist: User,
    make_user: UserFactory,
    db_session: AsyncSession,
) -> None:
    colleague = await make_user(UserRole.PSYCHOLOGIST)

    response = await api.patch(
        "/staff/profile",
        json={"user_id": str(colleague.id), "created_at": "2000-01-01T00:00:00Z"},
    )

    assert response.status_code == 200
    assert response.json()["user_id"] == str(psychologist.id)
    assert await audit_entries(db_session, AuditAction.PROFILE_UPDATED) == []


@pytest.mark.parametrize("field", ["title", "bio", "phone"])
async def test_nullable_fields_can_be_cleared(
    api: httpx.AsyncClient, psychologist: User, db_session: AsyncSession, field: str
) -> None:
    await api.patch("/staff/profile", json=CHANGES)

    response = await api.patch("/staff/profile", json={field: None})

    assert response.status_code == 200
    assert response.json()[field] is None
    assert getattr(await load_profile(db_session, psychologist), field) is None


@pytest.mark.parametrize("field", NOT_NULLABLE)
async def test_explicit_null_is_rejected_for_other_fields(
    api: httpx.AsyncClient, psychologist: User, field: str
) -> None:
    response = await api.patch("/staff/profile", json={field: None})

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "VALIDATION_ERROR"
    assert error["details"]["fields"] == [{"field": f"body.{field}", "code": "null_not_allowed"}]


@pytest.mark.parametrize(
    "change",
    [
        {"buffer_minutes": 0},
        {"buffer_minutes": 120},
        {"min_booking_notice_hours": 0},
        {"max_booking_advance_days": 1},
        {"cancellation_window_hours": 0},
        {"min_booking_notice_hours": 87_600},
        {"max_booking_advance_days": 3_650},
        {"cancellation_window_hours": 87_600},
    ],
)
async def test_numeric_fields_accept_the_limits_of_their_ranges(
    api: httpx.AsyncClient, psychologist: User, change: dict[str, int]
) -> None:
    response = await api.patch("/staff/profile", json=change)

    assert response.status_code == 200


@pytest.mark.parametrize(
    "change",
    [
        {"buffer_minutes": -1},
        {"buffer_minutes": 121},
        {"min_booking_notice_hours": -1},
        {"min_booking_notice_hours": 87_601},
        {"max_booking_advance_days": 0},
        {"max_booking_advance_days": 3_651},
        {"cancellation_window_hours": -1},
        {"cancellation_window_hours": 87_601},
        {"cancellation_window_hours": 2**31},
        {"first_name": ""},
        {"first_name": "   "},
        {"last_name": "a" * 101},
        {"title": ""},
        {"title": "a" * 101},
        {"bio": "a" * 5001},
        {"bio": "a\x00b"},
        {"slug": "Not A Slug"},
        {"slug": "a" * 101},
        {"phone": "not a phone"},
        {"auto_confirm_bookings": "sometimes"},
    ],
)
async def test_invalid_values_are_rejected(
    api: httpx.AsyncClient, psychologist: User, change: dict[str, Any]
) -> None:
    response = await api.patch("/staff/profile", json=change)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


@pytest.mark.parametrize("timezone", ["Europe/Istanbul", "America/New_York", "UTC"])
async def test_known_timezones_are_accepted(
    api: httpx.AsyncClient, psychologist: User, timezone: str
) -> None:
    response = await api.patch("/staff/profile", json={"timezone": timezone})

    assert response.status_code == 200
    assert response.json()["timezone"] == timezone


@pytest.mark.parametrize(
    "timezone",
    [
        "",
        "Mars/Olympus_Mons",
        "Europe",
        "europe/istanbul",
        "Europe/Istanbul ",
        "../etc/passwd",
        "/etc/passwd",
        "Etc/../UTC",
        "localtime",
        "a" * 65,
    ],
)
async def test_other_timezones_are_rejected(
    api: httpx.AsyncClient, psychologist: User, timezone: str
) -> None:
    response = await api.patch("/staff/profile", json={"timezone": timezone})

    assert response.status_code == 422


def test_a_timezone_key_that_does_not_load_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.modules.users.schemas._known_timezones", lambda: frozenset({"Mars/Olympus_Mons"})
    )

    with pytest.raises(ValueError, match="unknown_timezone"):
        UpdateProfileRequest(timezone="Mars/Olympus_Mons")


async def test_taken_slug_returns_409_and_changes_nothing(
    api: httpx.AsyncClient,
    psychologist: User,
    make_user: UserFactory,
    db_session: AsyncSession,
) -> None:
    colleague = await load_profile(db_session, await make_user(UserRole.PSYCHOLOGIST))
    own_slug = (await load_profile(db_session, psychologist)).slug

    response = await api.patch("/staff/profile", json={"slug": colleague.slug, "first_name": "New"})

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "SLUG_TAKEN"
    profile = await load_profile(db_session, psychologist)
    assert (profile.slug, profile.first_name) == (own_slug, "A")
    assert await audit_entries(db_session, AuditAction.PROFILE_UPDATED) == []


async def test_keeping_the_own_slug_is_not_a_conflict(
    api: httpx.AsyncClient, psychologist: User, db_session: AsyncSession
) -> None:
    slug = (await load_profile(db_session, psychologist)).slug

    response = await api.patch("/staff/profile", json={"slug": slug, "bio": "New"})

    assert response.status_code == 200
    assert response.json()["bio"] == "New"


async def test_update_is_audited_with_field_names_and_no_personal_data(
    api: httpx.AsyncClient, psychologist: User, db_session: AsyncSession
) -> None:
    await api.patch(
        "/staff/profile", json={"first_name": "Secret", "phone": "+90 532 123 45 67", "bio": None}
    )

    [entry] = await audit_entries(db_session, AuditAction.PROFILE_UPDATED)
    assert entry.actor_user_id == psychologist.id
    assert entry.entity_type == "psychologist_profile"
    assert entry.entity_id == psychologist.id
    assert str(entry.ip_address) == "127.0.0.1"
    assert entry.event_metadata == {"changed_fields": ["first_name", "phone"]}


@pytest.mark.parametrize("body", [{}, {"first_name": "A", "buffer_minutes": 0}])
async def test_patch_that_changes_nothing_is_not_audited(
    api: httpx.AsyncClient,
    psychologist: User,
    db_session: AsyncSession,
    body: dict[str, Any],
) -> None:
    response = await api.patch("/staff/profile", json=body)

    assert response.status_code == 200
    assert await audit_entries(db_session, AuditAction.PROFILE_UPDATED) == []


class TestAccess:
    @pytest.mark.parametrize("role", [UserRole.ADMIN, UserRole.CLIENT])
    async def test_other_roles_get_403(
        self, new_client: ClientFactory, make_user: UserFactory, role: UserRole
    ) -> None:
        client = await new_client()
        await log_in(client, (await make_user(role)).email)

        read = await client.get("/staff/profile")
        write = await client.patch("/staff/profile", json={"bio": "x"})

        assert (read.status_code, write.status_code) == (403, 403)

    async def test_anonymous_callers_get_401(self, api: httpx.AsyncClient) -> None:
        read = await api.get("/staff/profile")
        write = await api.patch("/staff/profile", json={"bio": "x"})

        assert (read.status_code, write.status_code) == (401, 401)


class TestProfileServiceWithoutARouter:
    @pytest.fixture
    def service(self, db_session: AsyncSession) -> ProfileService:
        return build_profile_service(db_session, utcnow)

    @pytest.mark.parametrize("role", [UserRole.ADMIN, UserRole.CLIENT])
    async def test_enforces_the_role(self, service: ProfileService, role: UserRole) -> None:
        actor = Actor(user_id=uuid.uuid4(), role=role)

        with pytest.raises(ForbiddenError):
            await service.get_own(actor)
        with pytest.raises(ForbiddenError):
            await service.update_own(actor, UpdateProfileRequest(bio="x"), ip_address=None)

    async def test_a_missing_profile_is_not_found(self, service: ProfileService) -> None:
        actor = Actor(user_id=uuid.uuid4(), role=UserRole.PSYCHOLOGIST)

        with pytest.raises(NotFoundError):
            await service.get_own(actor)
