from typing import Any

import httpx
from fastapi import FastAPI
from sqlalchemy import event, text

from app.core.database import ReadSession, WriteSession
from app.core.errors import AppError


def record_transactions(app: FastAPI, events: list[str]) -> None:
    engine = app.state.database.engine.sync_engine
    event.listen(engine, "commit", lambda _: events.append("commit"))
    event.listen(engine, "rollback", lambda _: events.append("rollback"))


def add_routes(app: FastAPI) -> None:
    @app.post("/write")
    async def write(session: WriteSession) -> dict[str, bool]:
        await session.execute(text("SELECT 1"))
        return {"ok": True}

    @app.post("/write-fails")
    async def write_fails(session: WriteSession) -> None:
        await session.execute(text("SELECT 1"))
        raise AppError

    @app.get("/read")
    async def read(session: ReadSession) -> dict[str, int | None]:
        return {"value": await session.scalar(text("SELECT 1"))}


async def test_write_session_commits_before_response_starts(app: FastAPI) -> None:
    add_routes(app)
    events: list[str] = []
    record_transactions(app, events)

    async def recording_app(scope: Any, receive: Any, send: Any) -> None:
        async def recording_send(message: Any) -> None:
            if message["type"] == "http.response.start":
                events.append("response")
            await send(message)

        await app(scope, receive, recording_send)

    transport = httpx.ASGITransport(app=recording_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/write")

    assert response.status_code == 200
    assert events == ["commit", "response"]


async def test_write_session_rolls_back_on_error(app: FastAPI, client: httpx.AsyncClient) -> None:
    add_routes(app)
    events: list[str] = []
    record_transactions(app, events)

    response = await client.post("/write-fails")

    assert response.status_code == 400
    assert events == ["rollback"]


async def test_read_session_does_not_commit(app: FastAPI, client: httpx.AsyncClient) -> None:
    add_routes(app)
    events: list[str] = []
    record_transactions(app, events)

    response = await client.get("/read")

    assert response.json() == {"value": 1}
    assert "commit" not in events
