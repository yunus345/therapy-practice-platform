"""Field types and the base model shared by request schemas."""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated, ClassVar, Self

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    Field,
    StringConstraints,
    model_validator,
)
from pydantic_core import InitErrorDetails, PydanticCustomError, ValidationError

PHONE_PATTERN = r"^\+?[0-9][0-9 ()-]{8,18}[0-9]$"
# Postgres text cannot hold NUL, which would otherwise surface as a database error.
NO_NUL_PATTERN = r"^[^\x00]*$"
CENT = Decimal("0.01")
# Far enough from the limits of `datetime` that adding a duration or changing the
# timezone cannot overflow.
EARLIEST_DATETIME = datetime(1900, 1, 1, tzinfo=UTC)
LATEST_DATETIME = datetime(2200, 1, 1, tzinfo=UTC)


def bounded_text(max_length: int) -> StringConstraints:
    """Constraints for free text: stripped, not blank, and safe to store."""
    return StringConstraints(
        strip_whitespace=True, min_length=1, max_length=max_length, pattern=NO_NUL_PATTERN
    )


def _round_to_cents(value: Decimal) -> Decimal:
    """Give every amount the scale of numeric(10,2), so that 100 and 100.00 read the same."""
    return value.quantize(CENT)


def _normalize_to_utc(value: datetime) -> datetime:
    if not EARLIEST_DATETIME <= value <= LATEST_DATETIME:
        raise PydanticCustomError("datetime_out_of_range", "This date is out of range.")
    return value.astimezone(UTC)


PersonName = Annotated[str, bounded_text(100)]
Phone = Annotated[str, StringConstraints(strip_whitespace=True, pattern=PHONE_PATTERN)]
Money = Annotated[
    Decimal, Field(ge=0, max_digits=10, decimal_places=2), AfterValidator(_round_to_cents)
]
CurrencyCode = Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")]
ProfileSlug = Annotated[str, StringConstraints(max_length=100, pattern=r"^[a-z0-9]+(-[a-z0-9]+)*$")]
UtcDatetime = Annotated[AwareDatetime, AfterValidator(_normalize_to_utc)]


class PatchModel(BaseModel):
    """Body of a PATCH route. Read it with `model_dump(exclude_unset=True)`.

    Fields default to None so that a body can leave them out. An explicit null is valid
    only for the fields in `nullable_fields`, because it would break a NOT NULL column.
    """

    nullable_fields: ClassVar[frozenset[str]] = frozenset()

    @model_validator(mode="after")
    def reject_explicit_nulls(self) -> Self:
        rejected = sorted(
            name
            for name in self.model_fields_set - self.nullable_fields
            if getattr(self, name) is None
        )
        if rejected:
            raise ValidationError.from_exception_data(
                type(self).__name__,
                [
                    InitErrorDetails(
                        type=PydanticCustomError("null_not_allowed", "This field cannot be null."),
                        loc=(name,),
                        input=None,
                    )
                    for name in rejected
                ],
            )
        return self
