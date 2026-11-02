import pytest

from app.modules.users.models import User, UserRole
from tests.conftest import UserFactory


@pytest.fixture
async def therapist(make_user: UserFactory) -> User:
    """A psychologist who is not logged in, as the target of an admin's actions."""
    return await make_user(UserRole.PSYCHOLOGIST)
