import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from app.core.schemas import CurrencyCode, Money, PatchModel, bounded_text
from app.modules.catalog.models import DeliveryMode

ServiceName = Annotated[str, bounded_text(200)]
ServiceDescription = Annotated[str, bounded_text(2000)]
DurationMinutes = Annotated[int, Field(ge=1, le=480)]


class CreateServiceRequest(BaseModel):
    name: ServiceName
    description: ServiceDescription | None = None
    duration_minutes: DurationMinutes
    price_amount: Money
    currency: CurrencyCode = "TRY"
    delivery_mode: DeliveryMode


class UpdateServiceRequest(PatchModel):
    nullable_fields = frozenset({"description"})

    name: ServiceName | None = None
    description: ServiceDescription | None = None
    duration_minutes: DurationMinutes | None = None
    price_amount: Money | None = None
    currency: CurrencyCode | None = None
    delivery_mode: DeliveryMode | None = None
    is_active: bool | None = None


class AssignServiceRequest(BaseModel):
    custom_price_amount: Money | None = None


class ServiceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    description: str | None
    duration_minutes: int
    price_amount: Decimal
    currency: str
    delivery_mode: DeliveryMode
    is_active: bool
    created_at: datetime
    updated_at: datetime


class OfferedServiceResponse(BaseModel):
    """A service as one psychologist offers it: `price_amount` is the effective price."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    description: str | None
    duration_minutes: int
    price_amount: Decimal
    currency: str
    delivery_mode: DeliveryMode


class AssignedServiceResponse(OfferedServiceResponse):
    custom_price_amount: Decimal | None
    is_active: bool
