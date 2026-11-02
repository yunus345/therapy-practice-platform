"""Query parameters and response shape shared by list endpoints."""

from dataclasses import dataclass
from typing import Annotated, Self

from fastapi import Depends, Query
from pydantic import BaseModel

DEFAULT_LIMIT = 50
MAX_LIMIT = 100
# OFFSET is a bigint in Postgres, so a larger value would fail in the database.
MAX_OFFSET = 2**63 - 1


@dataclass(frozen=True)
class PageParams:
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT
    offset: Annotated[int, Query(ge=0, le=MAX_OFFSET)] = 0


PageParamsDep = Annotated[PageParams, Depends()]


class Page[T](BaseModel):
    items: list[T]
    total: int
    limit: int
    offset: int

    @classmethod
    def of(cls, items: list[T], total: int, params: PageParams) -> Self:
        return cls(items=items, total=total, limit=params.limit, offset=params.offset)
