import httpx
from fastapi import Depends, FastAPI, Request

from app.core.rate_limit import InMemoryRateLimitBackend, RateLimit


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


async def test_backend_allows_up_to_the_limit() -> None:
    backend = InMemoryRateLimitBackend(clock=FakeClock())

    results = [await backend.hit("key", limit=2, window_seconds=60) for _ in range(3)]

    assert [result.allowed for result in results] == [True, True, False]
    assert results[2].retry_after_seconds == 60


async def test_backend_window_slides() -> None:
    clock = FakeClock()
    backend = InMemoryRateLimitBackend(clock=clock)
    await backend.hit("key", limit=1, window_seconds=60)

    clock.now += 59
    assert not (await backend.hit("key", limit=1, window_seconds=60)).allowed

    clock.now += 1
    assert (await backend.hit("key", limit=1, window_seconds=60)).allowed


async def test_backend_keys_are_independent() -> None:
    backend = InMemoryRateLimitBackend(clock=FakeClock())

    await backend.hit("first", limit=1, window_seconds=60)

    assert (await backend.hit("second", limit=1, window_seconds=60)).allowed


async def test_backend_forgets_expired_keys() -> None:
    clock = FakeClock()
    backend = InMemoryRateLimitBackend(clock=clock)
    await backend.hit("old", limit=1, window_seconds=60)

    clock.now += 61
    await backend.hit("new", limit=1, window_seconds=60)

    assert set(backend._hits) == {"new"}


async def test_route_returns_429_with_retry_after(app: FastAPI, client: httpx.AsyncClient) -> None:
    @app.post("/limited", dependencies=[Depends(RateLimit("limited", limit=1, window_seconds=60))])
    async def limited() -> dict[str, bool]:
        return {"ok": True}

    assert (await client.post("/limited")).status_code == 200
    response = await client.post("/limited")

    assert response.status_code == 429
    assert response.json()["error"]["code"] == "RATE_LIMITED"
    assert int(response.headers["Retry-After"]) >= 1


async def test_route_scopes_do_not_share_counters(app: FastAPI, client: httpx.AsyncClient) -> None:
    @app.post("/a", dependencies=[Depends(RateLimit("a", limit=1, window_seconds=60))])
    async def route_a() -> None:
        return None

    @app.post("/b", dependencies=[Depends(RateLimit("b", limit=1, window_seconds=60))])
    async def route_b() -> None:
        return None

    assert (await client.post("/a")).status_code == 200
    assert (await client.post("/b")).status_code == 200


async def test_route_can_use_a_custom_key(app: FastAPI, client: httpx.AsyncClient) -> None:
    def by_header(request: Request) -> str:
        return request.headers["X-Key"]

    limit = RateLimit("custom", limit=1, window_seconds=60, key=by_header)

    @app.post("/custom", dependencies=[Depends(limit)])
    async def custom() -> None:
        return None

    assert (await client.post("/custom", headers={"X-Key": "a"})).status_code == 200
    assert (await client.post("/custom", headers={"X-Key": "b"})).status_code == 200
    assert (await client.post("/custom", headers={"X-Key": "a"})).status_code == 429
