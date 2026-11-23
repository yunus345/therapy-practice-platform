from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from typing import Annotated, Any

import pytest
from pydantic import TypeAdapter, ValidationError

from app.core.schemas import (
    CurrencyCode,
    Money,
    PatchModel,
    PersonName,
    Phone,
    ProfileSlug,
    StrictTrue,
    UtcDatetime,
    bounded_text,
)


class ClientPatch(PatchModel):
    nullable_fields = frozenset({"note"})

    name: PersonName | None = None
    phone: Phone | None = None
    note: str | None = None


class TestPatchModel:
    def test_absent_fields_stay_unset(self) -> None:
        patch = ClientPatch.model_validate({"name": "Ada"})

        assert patch.model_dump(exclude_unset=True) == {"name": "Ada"}

    def test_explicit_null_is_kept_for_a_nullable_field(self) -> None:
        patch = ClientPatch.model_validate({"note": None})

        assert patch.model_dump(exclude_unset=True) == {"note": None}

    def test_explicit_null_is_rejected_for_any_other_field(self) -> None:
        with pytest.raises(ValidationError) as error:
            ClientPatch.model_validate({"note": None, "name": None, "phone": None})

        assert [(item["loc"], item["type"]) for item in error.value.errors()] == [
            (("name",), "null_not_allowed"),
            (("phone",), "null_not_allowed"),
        ]

    def test_field_constraints_still_apply(self) -> None:
        with pytest.raises(ValidationError):
            ClientPatch.model_validate({"name": "   "})

    def test_no_field_is_nullable_by_default(self) -> None:
        class Strict(PatchModel):
            name: str | None = None

        with pytest.raises(ValidationError):
            Strict.model_validate({"name": None})


class TestPersonName:
    adapter: TypeAdapter[str] = TypeAdapter(PersonName)

    def test_strips_surrounding_whitespace(self) -> None:
        assert self.adapter.validate_python("  Ada Lovelace ") == "Ada Lovelace"

    @pytest.mark.parametrize("length", [1, 100])
    def test_accepts_the_limits_of_the_range(self, length: int) -> None:
        assert self.adapter.validate_python("a" * length) == "a" * length

    @pytest.mark.parametrize("value", ["", "   ", "a" * 101, "a\x00b"])
    def test_rejects_invalid_names(self, value: str) -> None:
        with pytest.raises(ValidationError):
            self.adapter.validate_python(value)

    def test_length_is_measured_after_stripping(self) -> None:
        assert self.adapter.validate_python(f" {'a' * 100} ") == "a" * 100


def test_bounded_text_allows_line_breaks_inside_the_text() -> None:
    adapter: TypeAdapter[str] = TypeAdapter(Annotated[str, bounded_text(20)])

    assert adapter.validate_python(" first\nsecond ") == "first\nsecond"


class TestPhone:
    adapter: TypeAdapter[str] = TypeAdapter(Phone)

    def test_strips_surrounding_whitespace(self) -> None:
        assert self.adapter.validate_python("  +90 532 123 45 67 ") == "+90 532 123 45 67"

    @pytest.mark.parametrize("value", ["0532 123 45 67", "0532-123-45-67", "+905321234567"])
    def test_accepts_common_formats(self, value: str) -> None:
        assert self.adapter.validate_python(value) == value

    @pytest.mark.parametrize(
        "value", ["", "12345", "phone number", "+90 532 123 45 67 x", "1" * 30]
    )
    def test_rejects_invalid_numbers(self, value: str) -> None:
        with pytest.raises(ValidationError):
            self.adapter.validate_python(value)


class TestMoney:
    adapter: TypeAdapter[Decimal] = TypeAdapter(Money)

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("0", "0.00"),
            (0, "0.00"),
            ("1500", "1500.00"),
            (1500, "1500.00"),
            ("1500.5", "1500.50"),
            (1500.5, "1500.50"),
            ("1E+2", "100.00"),
            ("99999999.99", "99999999.99"),
        ],
    )
    def test_gives_every_amount_two_decimal_places(self, value: Any, expected: str) -> None:
        amount = self.adapter.validate_python(value)

        assert isinstance(amount, Decimal)
        assert str(amount) == expected

    @pytest.mark.parametrize(
        "value", ["-0.01", "0.001", "100000000", "123456789.12", "NaN", "Infinity", "free"]
    )
    def test_rejects_amounts_that_do_not_fit(self, value: str) -> None:
        with pytest.raises(ValidationError):
            self.adapter.validate_python(value)


class TestCurrencyCode:
    adapter: TypeAdapter[str] = TypeAdapter(CurrencyCode)

    @pytest.mark.parametrize("value", ["TRY", "EUR"])
    def test_accepts_three_capital_letters(self, value: str) -> None:
        assert self.adapter.validate_python(value) == value

    @pytest.mark.parametrize("value", ["try", "TR", "TRYY", "T1Y", "TRY\n", ""])
    def test_rejects_anything_else(self, value: str) -> None:
        with pytest.raises(ValidationError):
            self.adapter.validate_python(value)


class TestProfileSlug:
    adapter: TypeAdapter[str] = TypeAdapter(ProfileSlug)

    @pytest.mark.parametrize("value", ["ada", "ada-lovelace", "dr-ada-2", "a" * 100])
    def test_accepts_lowercase_words_joined_by_hyphens(self, value: str) -> None:
        assert self.adapter.validate_python(value) == value

    @pytest.mark.parametrize(
        "value", ["", "Ada", "ada lovelace", "-ada", "ada-", "ada--lovelace", "ada_l", "a" * 101]
    )
    def test_rejects_anything_else(self, value: str) -> None:
        with pytest.raises(ValidationError):
            self.adapter.validate_python(value)


class TestUtcDatetime:
    adapter: TypeAdapter[datetime] = TypeAdapter(UtcDatetime)

    def test_keeps_the_instant_and_normalizes_the_offset(self) -> None:
        value = self.adapter.validate_python("2026-12-01T09:00:00+03:00")

        assert value == datetime(2026, 12, 1, 6, 0, tzinfo=UTC)
        assert value.utcoffset() == timedelta(0)

    def test_accepts_utc_as_it_is(self) -> None:
        value = self.adapter.validate_python("2026-12-01T09:00:00Z")

        assert value == datetime(2026, 12, 1, 9, 0, tzinfo=UTC)

    def test_rejects_a_time_without_an_offset(self) -> None:
        with pytest.raises(ValidationError) as error:
            self.adapter.validate_python("2026-12-01T09:00:00")

        assert error.value.errors()[0]["type"] == "timezone_aware"

    @pytest.mark.parametrize(
        "value",
        [
            datetime(1900, 1, 1, tzinfo=UTC),
            datetime(2200, 1, 1, tzinfo=UTC),
        ],
    )
    def test_accepts_the_limits_of_the_range(self, value: datetime) -> None:
        assert self.adapter.validate_python(value) == value

    @pytest.mark.parametrize(
        "value",
        [
            datetime(1899, 12, 31, 23, 59, 59, tzinfo=UTC),
            datetime(2200, 1, 1, 0, 0, 1, tzinfo=UTC),
            datetime(1, 1, 1, tzinfo=timezone(timedelta(hours=3))),
            datetime(9999, 12, 31, 23, 59, 59, tzinfo=timezone(timedelta(hours=-3))),
        ],
        ids=["before_the_range", "after_the_range", "first_instant", "last_instant"],
    )
    def test_rejects_instants_where_arithmetic_could_overflow(self, value: datetime) -> None:
        with pytest.raises(ValidationError) as error:
            self.adapter.validate_python(value)

        assert error.value.errors()[0]["type"] == "datetime_out_of_range"


class TestStrictTrue:
    adapter: TypeAdapter[bool] = TypeAdapter(StrictTrue)

    def test_accepts_true(self) -> None:
        assert self.adapter.validate_json("true") is True

    @pytest.mark.parametrize("given", ["false", "1", "0", '"true"', "null"])
    def test_rejects_anything_but_true(self, given: str) -> None:
        with pytest.raises(ValidationError):
            self.adapter.validate_json(given)

    def test_documents_the_constant(self) -> None:
        assert self.adapter.json_schema() == {"type": "boolean", "const": True}
