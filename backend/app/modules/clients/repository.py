import uuid
from collections.abc import Collection, Sequence

from sqlalchemy import ColumnElement, Row, Select, Text, and_, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import QueryableAttribute

from app.modules.clients.models import Client, PsychologistClient

SEARCHED_COLUMNS = (Client.first_name, Client.last_name, Client.email, Client.phone)

# Turkish has a dotted and a dotless i, and the database collation lowercases "I" to "i"
# where Turkish expects the dotless one. Folding every form of the letter to "i" on both
# sides lets a search match whichever form was typed or stored, at the cost of a word
# with a dotless i also finding its dotted twin.
DOTLESS_I = "\N{LATIN SMALL LETTER DOTLESS I}"
DOTTED_CAPITAL_I = "\N{LATIN CAPITAL LETTER I WITH DOT ABOVE}"
I_FORMS = "I" + DOTLESS_I + DOTTED_CAPITAL_I
I_FOLDED = "i" * len(I_FORMS)
FOLD_I = str.maketrans(I_FORMS, I_FOLDED)


def _fold(term: str) -> str:
    return term.translate(FOLD_I).lower()


def _folded(column: QueryableAttribute[str | None]) -> ColumnElement[str]:
    return func.lower(func.translate(column, I_FORMS, I_FOLDED, type_=Text))


def _matches(term: str) -> ColumnElement[bool]:
    folded = _fold(term)
    return or_(*(_folded(column).contains(folded, autoescape=True) for column in SEARCHED_COLUMNS))


def _visible(psychologist_id: uuid.UUID | None, terms: Sequence[str] = ()) -> Select[Client]:
    """Clients that are not deleted: all of them, or those one psychologist has in care."""
    statement = select(Client).where(Client.deleted_at.is_(None), *map(_matches, terms))
    if psychologist_id is None:
        return statement
    return statement.join(
        PsychologistClient,
        and_(
            PsychologistClient.client_id == Client.id,
            PsychologistClient.psychologist_id == psychologist_id,
            PsychologistClient.ended_at.is_(None),
        ),
    )


class ClientRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, client: Client) -> None:
        self._session.add(client)
        await self._session.flush()

    async def flush(self) -> None:
        await self._session.flush()

    async def list_visible(
        self, psychologist_id: uuid.UUID | None, terms: Sequence[str], limit: int, offset: int
    ) -> Sequence[Client]:
        result = await self._session.scalars(
            _visible(psychologist_id, terms)
            .order_by(Client.last_name, Client.first_name, Client.id)
            .limit(limit)
            .offset(offset)
        )
        return result.all()

    async def count_visible(self, psychologist_id: uuid.UUID | None, terms: Sequence[str]) -> int:
        visible = _visible(psychologist_id, terms).subquery()
        return await self._session.scalar(select(func.count()).select_from(visible)) or 0

    async def get_visible(
        self, psychologist_id: uuid.UUID | None, client_id: uuid.UUID
    ) -> Client | None:
        return await self._session.scalar(
            _visible(psychologist_id)
            .where(Client.id == client_id)
            .execution_options(populate_existing=True)
        )

    async def get_by_user(self, user_id: uuid.UUID) -> Client | None:
        return await self._session.scalar(select(Client).where(Client.user_id == user_id))

    async def get_names(self, ids: Collection[uuid.UUID]) -> Sequence[Row[uuid.UUID, str, str]]:
        result = await self._session.execute(
            select(Client.id, Client.first_name, Client.last_name).where(Client.id.in_(ids))
        )
        return result.all()

    async def lock(self, client_id: uuid.UUID, *, allow_references: bool) -> Client | None:
        """Take the lock that serializes care changes and read the row under it.

        A foreign key check holds FOR KEY SHARE on the row until its transaction ends.
        Plain FOR UPDATE therefore waits for an appointment being inserted and stops new
        ones, while `allow_references` selects FOR NO KEY UPDATE, which lets them pass.
        """
        return await self._session.scalar(
            select(Client)
            .where(Client.id == client_id)
            .with_for_update(key_share=allow_references)
            .execution_options(populate_existing=True)
        )

    async def get_active_relationship(
        self, psychologist_id: uuid.UUID, client_id: uuid.UUID
    ) -> PsychologistClient | None:
        return await self._session.scalar(
            select(PsychologistClient)
            .where(
                PsychologistClient.psychologist_id == psychologist_id,
                PsychologistClient.client_id == client_id,
                PsychologistClient.ended_at.is_(None),
            )
            .execution_options(populate_existing=True)
        )

    async def has_active_relationship(self, client_id: uuid.UUID) -> bool:
        statement = (
            select(PsychologistClient.id)
            .where(PsychologistClient.client_id == client_id, PsychologistClient.ended_at.is_(None))
            .limit(1)
        )
        return await self._session.scalar(statement) is not None

    async def has_relationship_row(self, psychologist_id: uuid.UUID, client_id: uuid.UUID) -> bool:
        statement = select(PsychologistClient.id).where(
            PsychologistClient.psychologist_id == psychologist_id,
            PsychologistClient.client_id == client_id,
        )
        return await self._session.scalar(statement) is not None

    async def upsert_relationship(self, psychologist_id: uuid.UUID, client_id: uuid.UUID) -> None:
        statement = insert(PsychologistClient).values(
            psychologist_id=psychologist_id, client_id=client_id
        )
        await self._session.execute(
            statement.on_conflict_do_update(
                index_elements=["psychologist_id", "client_id"], set_={"ended_at": None}
            )
        )
