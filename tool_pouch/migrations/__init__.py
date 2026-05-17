"""Versioned SQLite migrations.

Each migration is a `.sql` file numbered `NNN_<slug>.sql`. Apply order is
filename order. The current applied version is tracked in the
`schema_version` table.

Idempotent: running `apply_pending` twice is a no-op past the first run.

Why versioned migrations exist:
    The 0.1 store used `CREATE TABLE IF NOT EXISTS`, which silently skips
    schema changes on existing databases. Adding columns post-0.1 (e.g.
    `runs.kind`) requires real `ALTER TABLE` statements gated by version.
"""
from __future__ import annotations

import sqlite3
from importlib import resources
from pathlib import Path
from typing import List


_VERSION_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY
);
"""


def _split_statements(sql: str) -> List[str]:
    """Split a migration file into individual statements.

    Naive `;` split is sufficient because our migrations are simple DDL +
    DML and never contain semicolons in literals. Comment lines are kept
    inside their statement (sqlite parses them fine).
    """
    out: List[str] = []
    for raw in sql.split(";"):
        stripped = raw.strip()
        if stripped:
            out.append(stripped)
    return out


def _list_migrations() -> List[tuple[int, str, List[str]]]:
    """Return [(version, name, statements), ...] sorted by version.

    Reads `.sql` files packaged alongside this module. Filename convention:
        NNN_slug.sql
    """
    files = resources.files(__package__)
    out: List[tuple[int, str, List[str]]] = []
    for entry in files.iterdir():
        name = entry.name
        if not name.endswith(".sql"):
            continue
        version_str, _, _ = name.partition("_")
        try:
            version = int(version_str)
        except ValueError:
            continue
        sql = entry.read_text(encoding="utf-8")
        out.append((version, name, _split_statements(sql)))
    out.sort(key=lambda t: t[0])
    return out


def current_version(conn: sqlite3.Connection) -> int:
    """Return the highest applied migration version, or 0 for fresh DBs."""
    conn.execute(_VERSION_TABLE_SQL.strip().rstrip(";"))
    conn.commit()
    row = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
    return (row[0] or 0) if row else 0


def apply_pending(conn: sqlite3.Connection) -> List[int]:
    """Apply every migration whose version is greater than the current.

    Returns the list of versions actually applied. Each migration runs in
    its own transaction so a failure rolls back cleanly. We avoid
    executescript() because it issues an implicit COMMIT that defeats the
    transaction wrap.
    """
    applied: List[int] = []
    current = current_version(conn)
    for version, _name, statements in _list_migrations():
        if version <= current:
            continue
        with conn:
            for stmt in statements:
                conn.execute(stmt)
            conn.execute(
                "INSERT INTO schema_version (version) VALUES (?)", (version,)
            )
        applied.append(version)
    return applied
