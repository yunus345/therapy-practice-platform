"""Module boundaries from CLAUDE.md, checked on the source code with `ast`."""

import ast
from collections.abc import Iterator
from pathlib import Path, PurePosixPath

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
MODULES_PACKAGE = ("app", "modules")


def imported_names(tree: ast.Module, package: tuple[str, ...]) -> Iterator[str]:
    """Yield every imported dotted name, resolving relative imports against `package`."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            yield from (alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            anchor = package[: len(package) - node.level + 1] if node.level else ()
            base = ".".join((*anchor, *(node.module.split(".") if node.module else ())))
            yield base
            yield from (f"{base}.{alias.name}" for alias in node.names)


def is_service_module(name: str) -> bool:
    return name == "service" or name.endswith("_service")


def find_violations(path: PurePosixPath, source: str) -> list[str]:
    """Return one message per import in `source` that breaks a boundary rule."""
    in_a_module = path.parts[:2] == MODULES_PACKAGE and len(path.parts) > 3
    owner = path.parts[2] if in_a_module else None
    violations: set[str] = set()

    for name in imported_names(ast.parse(source), path.parts[:-1]):
        parts = name.split(".")
        imports_a_repository = "repository" in parts

        if imports_a_repository and tuple(parts[:2]) == MODULES_PACKAGE and parts[2] != owner:
            violations.add(f"{path}: {name} is the repository of another module")
        if path.name == "router.py" and (imports_a_repository or parts[0] == "sqlalchemy"):
            violations.add(f"{path}: a router must not import {name}")
        if path.name == "repository.py" and (
            parts[0] == "fastapi" or any(is_service_module(part) for part in parts)
        ):
            violations.add(f"{path}: a repository must not import {name}")

    return sorted(violations)


def test_application_code_respects_the_module_boundaries() -> None:
    files = sorted((BACKEND_DIR / "app").rglob("*.py"))
    # Without this, a wrong path would make the test pass on nothing.
    assert BACKEND_DIR / "app" / "modules" / "users" / "router.py" in files

    violations = [
        violation
        for file in files
        for violation in find_violations(
            PurePosixPath(file.relative_to(BACKEND_DIR).as_posix()),
            file.read_text(encoding="utf-8"),
        )
    ]

    assert violations == []


def test_clients_reach_scheduling_only_through_their_router() -> None:
    clients_dir = BACKEND_DIR / "app" / "modules" / "clients"
    files = sorted(set(clients_dir.glob("*.py")) - {clients_dir / "router.py"})
    # Without this, a wrong path would make the test pass on nothing.
    assert clients_dir / "service.py" in files

    imported = [
        name
        for file in files
        for name in imported_names(
            ast.parse(file.read_text(encoding="utf-8")), ("app", "modules", "clients")
        )
    ]

    assert [name for name in imported if name.startswith("app.modules.scheduling")] == []


@pytest.mark.parametrize(
    ("path", "source"),
    [
        # A repository belongs to its own module.
        ("app/modules/auth/service.py", "from app.modules.users.repository import UserRepository"),
        ("app/modules/auth/service.py", "import app.modules.users.repository"),
        ("app/modules/auth/service.py", "from app.modules.users import repository"),
        ("app/modules/auth/service.py", "from ..users.repository import UserRepository"),
        ("app/modules/auth/service.py", "from ..users import repository as users"),
        ("app/modules/auth/service.py", "def f():\n    import app.modules.users.repository"),
        # Nothing outside the modules may reach a repository.
        ("app/cli.py", "from app.modules.users.repository import UserRepository"),
        ("app/core/database.py", "import app.modules.audit.repository"),
        # A router depends on services only.
        ("app/modules/users/router.py", "from app.modules.users.repository import UserRepository"),
        ("app/modules/users/router.py", "from .repository import UserRepository"),
        ("app/modules/users/router.py", "from . import repository"),
        ("app/modules/users/router.py", "from sqlalchemy import select"),
        ("app/modules/users/router.py", "import sqlalchemy.exc"),
        # A repository knows neither HTTP nor business logic.
        ("app/modules/users/repository.py", "from fastapi import Depends"),
        ("app/modules/users/repository.py", "import fastapi"),
        ("app/modules/users/repository.py", "from app.modules.users.service import UserService"),
        ("app/modules/users/repository.py", "from app.modules.audit.service import AuditService"),
        ("app/modules/users/repository.py", "from app.modules.users import admin_service"),
        ("app/modules/users/repository.py", "from .service import UserService"),
    ],
)
def test_boundary_check_reports_a_violation(path: str, source: str) -> None:
    assert find_violations(PurePosixPath(path), source)


@pytest.mark.parametrize(
    ("path", "source"),
    [
        ("app/modules/users/service.py", "from app.modules.users.repository import UserRepository"),
        ("app/modules/users/service.py", "from .repository import UserRepository"),
        ("app/modules/auth/service.py", "from app.modules.users.service import UserService"),
        ("app/modules/users/router.py", "from app.modules.users.service import UserService"),
        ("app/modules/users/repository.py", "from sqlalchemy import select"),
        ("app/modules/users/repository.py", "from app.modules.users.models import User"),
        ("app/modules/catalog/repository.py", "from .models import PsychologistService, Service"),
        ("app/core/database.py", "from fastapi import Depends"),
    ],
)
def test_boundary_check_accepts_allowed_imports(path: str, source: str) -> None:
    assert find_violations(PurePosixPath(path), source) == []
