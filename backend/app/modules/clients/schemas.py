import uuid
from datetime import date, datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, EmailStr, StringConstraints

from app.core.schemas import NO_NUL_PATTERN, PatchModel, PersonName, Phone, StrictTrue

ClientSearch = Annotated[str, StringConstraints(max_length=100, pattern=NO_NUL_PATTERN)]


class CreateClientRequest(BaseModel):
    first_name: PersonName
    last_name: PersonName
    phone: Phone
    email: EmailStr | None = None
    birth_date: date | None = None
    kvkk_disclosure_given: StrictTrue


class UpdateClientRequest(PatchModel):
    nullable_fields = frozenset({"email", "birth_date"})

    first_name: PersonName | None = None
    last_name: PersonName | None = None
    email: EmailStr | None = None
    phone: Phone | None = None
    birth_date: date | None = None


class ClientResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    first_name: str
    last_name: str
    email: str | None
    phone: str
    birth_date: date | None
    has_account: bool
    created_at: datetime
