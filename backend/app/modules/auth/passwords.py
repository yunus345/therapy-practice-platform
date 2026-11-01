"""Password acceptance rules."""

from functools import lru_cache
from pathlib import Path

from app.core import data
from app.core.errors import AppError

MIN_PASSWORD_LENGTH = 10
# Hashing cost grows with length, so the upper limit also stops abuse.
MAX_PASSWORD_LENGTH = 128


class WeakPasswordError(AppError):
    status_code = 422
    code = "WEAK_PASSWORD"
    message = "The password does not meet the requirements."


@lru_cache
def common_passwords() -> frozenset[str]:
    path = Path(data.__file__).parent / "common_passwords.txt"
    return frozenset(path.read_text(encoding="utf-8").splitlines())


def ensure_acceptable_password(password: str, email: str | None = None) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise WeakPasswordError(details={"reason": "too_short"})
    if len(password) > MAX_PASSWORD_LENGTH:
        raise WeakPasswordError(details={"reason": "too_long"})
    lowered = password.lower()
    if lowered in common_passwords() or lowered == (email or "").lower():
        raise WeakPasswordError(details={"reason": "too_common"})
