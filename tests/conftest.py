"""Shared fixtures. Each test gets an isolated SQLite db via $TOOL_POUCH_DB."""
import pytest

from tool_pouch.store import Store


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    """Point the store at a per-test SQLite file."""
    path = tmp_path / "tool_pouch.db"
    monkeypatch.setenv("TOOL_POUCH_DB", str(path))
    return path


@pytest.fixture
def store(db_path):
    return Store()
