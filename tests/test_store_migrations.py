"""Migration system: fresh-DB init, idempotency, 0.1.x upgrade path, WAL."""
import sqlite3

from tool_pouch import migrations
from tool_pouch.store import Store


def test_fresh_db_applies_all_migrations(db_path):
    Store()
    conn = sqlite3.connect(str(db_path))
    try:
        version = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
    finally:
        conn.close()
    assert version == 2  # 001_init + 002_add_kind currently shipped


def test_migrations_are_idempotent(db_path):
    Store()
    Store()  # second open — must not re-apply or fail

    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute("SELECT version FROM schema_version ORDER BY version").fetchall()
    finally:
        conn.close()
    assert [r[0] for r in rows] == [1, 2]


def test_upgrade_from_pre_migration_db(tmp_path, monkeypatch):
    """A 0.1.x DB exists with the old SCHEMA but no schema_version table.

    The migration runner must detect that and apply both #001 (no-op for
    already-existing tables thanks to IF NOT EXISTS) and #002 (adds kind).
    """
    path = tmp_path / "legacy.db"
    monkeypatch.setenv("TOOL_POUCH_DB", str(path))

    # Hand-build a 0.1-shaped DB
    conn = sqlite3.connect(str(path))
    try:
        conn.executescript(
            """
            CREATE TABLE runs (
                id TEXT PRIMARY KEY,
                started_at REAL,
                agent_name TEXT,
                user_input TEXT,
                workspace_id TEXT,
                user_id TEXT,
                agent_version TEXT,
                environment TEXT DEFAULT 'local',
                metadata TEXT
            );
            CREATE TABLE results (
                id TEXT PRIMARY KEY, run_id TEXT, scenario TEXT, target_tool TEXT,
                outcome TEXT, failure_type TEXT, trace TEXT, duration_ms INTEGER,
                workspace_id TEXT
            );
            INSERT INTO runs (id, started_at, agent_name, user_input)
            VALUES ('legacy-1', 1000.0, 'old_agent', 'hi');
            """
        )
        conn.commit()
    finally:
        conn.close()

    # Open via Store — migrations should run
    store = Store()
    cur = store.conn.execute("SELECT id, kind FROM runs WHERE id = ?", ("legacy-1",))
    legacy = cur.fetchone()
    assert legacy is not None
    assert legacy[1] == "test"  # backfilled


def test_kind_column_constrains_values(store):
    store.new_run("a", "x", kind="production")
    store.new_run("a", "x", kind="replay")

    try:
        store.new_run("a", "x", kind="not-a-real-kind")
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError for invalid kind")


def test_wal_mode_enabled(store):
    mode = store.conn.execute("PRAGMA journal_mode").fetchone()[0]
    # WAL is unavailable on some FS; tmpfs in CI is typical, but pytest's
    # tmp_path is on disk on Linux/macOS so this should hold there.
    assert mode in ("wal", "memory")


def test_apply_pending_returns_only_new_versions(db_path):
    """Calling apply_pending after a full sync should return [].

    Direct unit-level check on the migrations module.
    """
    Store()
    conn = sqlite3.connect(str(db_path))
    try:
        applied = migrations.apply_pending(conn)
    finally:
        conn.close()
    assert applied == []
