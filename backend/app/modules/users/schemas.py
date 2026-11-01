import uuid

from pydantic import BaseModel, EmailStr, Field

from app.modules.users.models import UserRole


class CreatePsychologistRequest(BaseModel):
    email: EmailStr
    first_name: str = Field(min_length=1, max_length=100)
    last_name: str = Field(min_length=1, max_length=100)
    slug: str = Field(pattern=r"^[a-z0-9]+(-[a-z0-9]+)*$", max_length=100)
    title: str | None = Field(default=None, max_length=100)


class AdminUserResponse(BaseModel):
    id: uuid.UUID
    role: UserRole
    is_active: bool
