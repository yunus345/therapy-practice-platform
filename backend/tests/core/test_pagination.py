import httpx
import pytest
from fastapi import FastAPI

from app.core.pagination import MAX_LIMIT, MAX_OFFSET, Page, PageParams, PageParamsDep


def add_items_route(app: FastAPI) -> None:
    @app.get("/items")
    async def items(page: PageParamsDep) -> Page[str]:
        return Page[str].of(["a", "b"], 7, page)


async def test_page_defaults(app: FastAPI, client: httpx.AsyncClient) -> None:
    add_items_route(app)

    response = await client.get("/items")

    assert response.json() == {"items": ["a", "b"], "total": 7, "limit": 50, "offset": 0}


async def test_page_accepts_the_limits_of_each_range(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    add_items_route(app)

    for limit, offset in [(1, 0), (MAX_LIMIT, MAX_OFFSET)]:
        response = await client.get("/items", params={"limit": limit, "offset": offset})

        assert response.status_code == 200
        assert (response.json()["limit"], response.json()["offset"]) == (limit, offset)


@pytest.mark.parametrize(
    "params",
    [
        {"limit": 0},
        {"limit": MAX_LIMIT + 1},
        {"limit": "many"},
        {"offset": -1},
        {"offset": MAX_OFFSET + 1},
    ],
)
async def test_page_rejects_values_outside_the_range(
    app: FastAPI, client: httpx.AsyncClient, params: dict[str, int | str]
) -> None:
    add_items_route(app)

    response = await client.get("/items", params=params)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_page_params_can_be_built_without_a_request() -> None:
    assert PageParams() == PageParams(limit=50, offset=0)
