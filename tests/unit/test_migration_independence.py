import ast
from pathlib import Path
from typing import cast

import pytest
from sqlalchemy import CheckConstraint, Table

from apps.api.app.models import entities as db

MIGRATION_DIR = Path("apps/api/alembic/versions")
HISTORICAL_REVISIONS = (
    "20260721_0001_initial.py",
    "20260721_0002_conversation_foundation.py",
    "20260721_0003_browser_readonly.py",
    "20260721_0006_scheduling.py",
)


@pytest.mark.parametrize("filename", HISTORICAL_REVISIONS)
def test_historical_migration_does_not_depend_on_application_models(filename: str) -> None:
    source = (MIGRATION_DIR / filename).read_text(encoding="utf-8")
    tree = ast.parse(source)

    imported_modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }
    imported_modules.update(
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    )

    assert not any(module.startswith("apps.api.app") for module in imported_modules)
    assert "Base.metadata" not in source
    assert "op.create_table" in source


def test_message_status_constraint_matches_platform_event_import() -> None:
    constraint = next(
        item
        for item in cast(Table, db.Message.__table__).constraints
        if item.name == "ck_messages_status"
    )

    assert "PLATFORM_EVENT_IGNORED" in str(
        cast(CheckConstraint, constraint).sqltext
    )
