import uuid
from functools import cache
from importlib import resources
from typing import Annotated
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AfterValidator, BaseModel, ConfigDict, EmailStr, Field, StringConstraints
from pydantic_core import PydanticCustomError

from app.core.schemas import PatchModel, PersonName, Phone, ProfileSlug, bounded_text
from app.modules.users.models import UserRole

MAX_BUFFER_MINUTES = 120
# The database sets no upper limit here. These keep date arithmetic on the values in range.
MAX_POLICY_HOURS = 87_600
MAX_POLICY_DAYS = 3_650


@cache
def _known_timezones() -> frozenset[str]:
    # The IANA names that `tzdata` ships, the same on every platform. The system zone
    # directory also holds files such as `localtime`, which follow the host's settings.
    zones = resources.files("tzdata").joinpath("zones").read_text(encoding="utf-8")
    return frozenset(zones.split())


def _zone_loads(key: str) -> bool:
    try:
        ZoneInfo(key)
    except ZoneInfoNotFoundError, ValueError, OSError:
        return False
    return True


def _ensure_known_timezone(value: str) -> str:
    # The set comes first, so that unchecked text never reaches the file lookup in ZoneInfo.
    if value not in _known_timezones() or not _zone_loads(value):
        raise PydanticCustomError("unknown_timezone", "This timezone is not known.")
    return value


ProfileTitle = Annotated[str, bounded_text(100)]
ProfileBio = Annotated[str, bounded_text(5000)]
Timezone = Annotated[str, StringConstraints(max_length=64), AfterValidator(_ensure_known_timezone)]


class CreatePsychologistRequest(BaseModel):
    email: EmailStr
    first_name: PersonName
    last_name: PersonName
    slug: ProfileSlug
    title: ProfileTitle | None = None


class AdminUserResponse(BaseModel):
    id: uuid.UUID
    role: UserRole
    is_active: bool


class UpdateProfileRequest(PatchModel):
    nullable_fields = frozenset({"title", "bio", "phone"})

    first_name: PersonName | None = None
    last_name: PersonName | None = None
    title: ProfileTitle | None = None
    slug: ProfileSlug | None = None
    bio: ProfileBio | None = None
    phone: Phone | None = None
    timezone: Timezone | None = None
    min_booking_notice_hours: int | None = Field(default=None, ge=0, le=MAX_POLICY_HOURS)
    max_booking_advance_days: int | None = Field(default=None, gt=0, le=MAX_POLICY_DAYS)
    cancellation_window_hours: int | None = Field(default=None, ge=0, le=MAX_POLICY_HOURS)
    auto_confirm_bookings: bool | None = None
    buffer_minutes: int | None = Field(default=None, ge=0, le=MAX_BUFFER_MINUTES)


class ProfileResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    user_id: uuid.UUID
    first_name: str
    last_name: str
    title: str | None
    slug: str
    bio: str | None
    phone: str | None
    timezone: str
    min_booking_notice_hours: int
    max_booking_advance_days: int
    cancellation_window_hours: int
    auto_confirm_bookings: bool
    buffer_minutes: int
