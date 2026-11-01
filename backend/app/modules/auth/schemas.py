import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, EmailStr, Field

from app.modules.auth.passwords import MAX_PASSWORD_LENGTH
from app.modules.users.models import UserRole

PHONE_PATTERN = r"^\+?[0-9][0-9 ()-]{8,18}[0-9]$"


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(max_length=MAX_PASSWORD_LENGTH)
    first_name: str = Field(min_length=1, max_length=100)
    last_name: str = Field(min_length=1, max_length=100)
    phone: str = Field(pattern=PHONE_PATTERN)
    kvkk_accepted: Literal[True]


class TokenRequest(BaseModel):
    token: str = Field(min_length=1, max_length=256)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(max_length=MAX_PASSWORD_LENGTH)


class PasswordResetRequest(BaseModel):
    email: EmailStr


class PasswordResetConfirm(BaseModel):
    token: str = Field(min_length=1, max_length=256)
    new_password: str = Field(max_length=MAX_PASSWORD_LENGTH)


class TwoFactorCodeRequest(BaseModel):
    code: str = Field(pattern=r"^[0-9]{6}$")


class TwoFactorVerifyRequest(BaseModel):
    code: str | None = Field(default=None, pattern=r"^[0-9]{6}$")
    recovery_code: str | None = Field(default=None, max_length=32)


class TwoFactorDisableRequest(BaseModel):
    password: str = Field(max_length=MAX_PASSWORD_LENGTH)
    code: str = Field(pattern=r"^[0-9]{6}$")


class AcceptedResponse(BaseModel):
    status: Literal["accepted"] = "accepted"


class CsrfResponse(BaseModel):
    csrf_token: str


class SessionStateResponse(BaseModel):
    csrf_token: str
    mfa_required: bool
    two_factor_setup_required: bool


class MeResponse(BaseModel):
    id: uuid.UUID
    email: str
    role: UserRole
    email_verified: bool
    totp_enabled: bool
    mfa_required: bool
    two_factor_setup_required: bool


class TwoFactorSetupResponse(BaseModel):
    provisioning_uri: str


class TwoFactorEnabledResponse(SessionStateResponse):
    recovery_codes: list[str]


class SessionResponse(BaseModel):
    id: uuid.UUID
    created_at: datetime
    last_seen_at: datetime
    expires_at: datetime
    ip_address: str | None
    user_agent: str | None
    is_current: bool
