import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.clients.models import Client, PsychologistClient
from app.modules.users.models import User, UserRole
from tests.conftest import ClientFactory, UserFactory, log_in


@pytest.fixture
async def colleague(make_user: UserFactory) -> User:
    """A second psychologist, who is not logged in."""
    return await make_user(UserRole.PSYCHOLOGIST)


@pytest.fixture
async def colleague_api(new_client: ClientFactory, colleague: User) -> httpx.AsyncClient:
    client = await new_client()
    await log_in(client, colleague.email)
    return client


async def load_client(session: AsyncSession, client: Client) -> Client:
    loaded = await session.get(Client, client.id, populate_existing=True)
    assert loaded is not None
    return loaded


async def load_relationship(
    session: AsyncSession, psychologist: User, client: Client
) -> PsychologistClient:
    relationship = await session.scalar(
        select(PsychologistClient)
        .where(
            PsychologistClient.psychologist_id == psychologist.id,
            PsychologistClient.client_id == client.id,
        )
        .execution_options(populate_existing=True)
    )
    assert relationship is not None
    return relationship
