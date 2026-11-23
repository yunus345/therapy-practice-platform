import json
from pathlib import Path

import pytest

from app.openapi import main, render


def test_render_is_the_api_document() -> None:
    document = json.loads(render())

    assert document["info"]["title"] == "Seans API"
    assert "/api/v1/health" in document["paths"]


def test_main_writes_the_document(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "openapi.json"
    monkeypatch.setattr("sys.argv", ["app.openapi", str(target)])

    main()

    assert target.read_text(encoding="utf-8") == render()


def test_main_requires_a_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.argv", ["app.openapi"])

    with pytest.raises(SystemExit):
        main()
