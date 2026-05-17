"""Storage layer for runs and results.

Designed so the local SQLite store and a future hosted backend share the
same data shape. UUID primary keys avoid collisions across users when
syncing, and multi-tenancy fields (workspace_id, user_id) are baked in now
so the cloud version doesn't require backfilling later.

Trace blobs are versioned (trace_schema_version) so the shape can evolve
without breaking old runs. The relational schema is versioned via the
`tool_pouch.migrations` package — see that module for the migration history.
"""
import os
import sqlite3
import json
import time
import uuid
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, List, Optional

from tool_pouch import migrations


TRACE_SCHEMA_VERSION = 1

# Run kinds. Used by the wrap/replay flow to keep test runs, captured
# production traces, and replay runs queryable independently.
KIND_TEST = "test"
KIND_PRODUCTION = "production"
KIND_REPLAY = "replay"
ALLOWED_KINDS = (KIND_TEST, KIND_PRODUCTION, KIND_REPLAY)


def default_db_path() -> Path:
    """Resolve the SQLite path: $TOOL_POUCH_DB > ~/.tool_pouch/tool_pouch.db.

    Storing under the user's home means `tool-pouch runs` and `tool-pouch show`
    work from any cwd, instead of only from the directory the run started
    in. Tests and CI override via $TOOL_POUCH_DB.
    """
    override = os.environ.get("TOOL_POUCH_DB")
    if override:
        return Path(override)
    return Path.home() / ".tool-pouch" / "tool_pouch.db"


def new_id():
    """Generate a new UUID for primary keys."""
    return str(uuid.uuid4())


class StoreInterface(ABC):
    """Abstract storage interface. Local and cloud implementations both satisfy this."""

    @abstractmethod
    def new_run(self, agent_name, user_input, **kwargs):
        """Create a run record. Returns the run_id."""

    @abstractmethod
    def add_result(self, run_id, scenario, target_tool, outcome, failure_type,
                   trace, duration_ms, **kwargs):
        """Record one scenario result for a run."""

    @abstractmethod
    def results_for(self, run_id):
        """Return all results for a run."""


class Store(StoreInterface):
    """Local SQLite store. Shipped with the OSS package, works fully offline.

    WAL mode is enabled at init so multiple processes (gunicorn / uvicorn
    workers, multiprocess pools) can write to the same DB without
    deadlocking. Schema is brought up to date by the versioned migration
    system in `tool_pouch.migrations`; running an older 0.1.x DB through this
    constructor upgrades it transparently.
    """

    def __init__(self, path: Optional[Any] = None):
        self.path = Path(path) if path else default_db_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(
            str(self.path),
            check_same_thread=False,
            timeout=10,
        )
        self._enable_wal()
        migrations.apply_pending(self.conn)

    def _enable_wal(self) -> None:
        """Set journal_mode=WAL for safe multi-process concurrency.

        WAL is a per-database setting persisted in the file header, so
        executing it once is enough — but it's cheap and idempotent, so
        we run it on every init to keep behavior consistent across
        processes that may be older binaries.
        """
        try:
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.execute("PRAGMA synchronous=NORMAL")
        except sqlite3.OperationalError:
            # Some filesystems (notably tmpfs and some network mounts)
            # reject WAL. Fall back silently — correctness is preserved,
            # only multi-process concurrency degrades.
            pass

    def new_run(
        self,
        agent_name,
        user_input,
        workspace_id=None,
        user_id=None,
        agent_version=None,
        environment="local",
        metadata=None,
        kind: str = KIND_TEST,
    ):
        if kind not in ALLOWED_KINDS:
            raise ValueError(
                f"kind must be one of {ALLOWED_KINDS}, got {kind!r}"
            )
        run_id = new_id()
        self.conn.execute(
            """INSERT INTO runs
               (id, started_at, agent_name, user_input, workspace_id, user_id,
                agent_version, environment, metadata, kind)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (run_id, time.time(), agent_name, user_input, workspace_id, user_id,
             agent_version, environment,
             json.dumps(metadata) if metadata else None,
             kind),
        )
        self.conn.commit()
        return run_id

    def add_result(self, run_id, scenario, target_tool, outcome, failure_type,
                   trace, duration_ms, workspace_id=None):
        # Always tag the trace with its schema version
        if isinstance(trace, dict) and "trace_schema_version" not in trace:
            trace["trace_schema_version"] = TRACE_SCHEMA_VERSION

        self.conn.execute(
            """INSERT INTO results
               (id, run_id, scenario, target_tool, outcome, failure_type,
                trace, duration_ms, workspace_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (new_id(), run_id, scenario, target_tool, outcome, failure_type,
             json.dumps(trace), duration_ms, workspace_id),
        )
        self.conn.commit()

    def results_for(self, run_id):
        # Allow short prefix lookup for CLI ergonomics (e.g. first 8 chars of UUID)
        full_id = self._resolve_run_id(run_id)
        if not full_id:
            return []

        rows = self.conn.execute(
            """SELECT scenario, target_tool, outcome, failure_type, trace
               FROM results WHERE run_id = ?""",
            (full_id,),
        ).fetchall()
        return [
            {"scenario": r[0], "target_tool": r[1], "outcome": r[2],
             "failure_type": r[3], "trace": json.loads(r[4])}
            for r in rows
        ]

    def _resolve_run_id(self, run_id):
        """Accept either a full UUID or a unique prefix, return the full UUID."""
        # Exact match first (fast path)
        row = self.conn.execute(
            "SELECT id FROM runs WHERE id = ?", (run_id,)
        ).fetchone()
        if row:
            return row[0]

        # Prefix match - return only if exactly one match
        rows = self.conn.execute(
            "SELECT id FROM runs WHERE id LIKE ?", (run_id + "%",)
        ).fetchall()
        if len(rows) == 1:
            return rows[0][0]
        return None

    def latest_run_id(self):
        """Return the most recent run's full UUID, or None if no runs exist."""
        row = self.conn.execute(
            "SELECT id FROM runs ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
        return row[0] if row else None

    def prune_old_traces(self, days: int) -> int:
        """Delete production traces older than `days`. Returns row count.

        Test runs (`kind='test'`) and replay runs (`kind='replay'`) are
        intentionally untouched — devs typically review test output and
        keep replay records for incident retros. `days <= 0` is a no-op.

        Pruning runs first cascades the linked results via run_id.
        """
        if days <= 0:
            return 0

        cutoff = time.time() - (days * 86400)
        cur = self.conn.execute(
            """SELECT id FROM runs
               WHERE kind = ? AND started_at IS NOT NULL AND started_at < ?""",
            (KIND_PRODUCTION, cutoff),
        )
        run_ids = [row[0] for row in cur.fetchall()]
        if not run_ids:
            return 0

        placeholders = ",".join("?" * len(run_ids))
        with self.conn:
            self.conn.execute(
                f"DELETE FROM results WHERE run_id IN ({placeholders})",
                run_ids,
            )
            self.conn.execute(
                f"DELETE FROM runs WHERE id IN ({placeholders})",
                run_ids,
            )
        return len(run_ids)

    def list_traces(
        self,
        kind: str = KIND_PRODUCTION,
        limit: Optional[int] = 50,
        agent_name: Optional[str] = None,
        since_seconds: Optional[float] = None,
        failed_only: bool = False,
        request_id: Optional[str] = None,
    ) -> List[dict]:
        """List captured traces with optional filters.

        Each row: {id, started_at, agent_name, agent_version, kind,
        outcome, failure_type, request_id}. The query joins the single
        result row that captured-mode (wrap) writes per run.

        Filters:
            agent_name     exact match
            since_seconds  only traces newer than this many seconds
            failed_only    only outcome != 'completed' OR failure-typed
            request_id     exact match against runs.metadata->>request_id
        """
        clauses = ["r.kind = ?"]
        params: List[Any] = [kind]

        if agent_name is not None:
            clauses.append("r.agent_name = ?")
            params.append(agent_name)

        if since_seconds is not None:
            clauses.append("r.started_at >= ?")
            params.append(time.time() - since_seconds)

        if request_id is not None:
            # SQLite JSON1 is reliably available since 3.38; fall back to
            # LIKE for older builds. The LIKE form is exact-match-ish and
            # safe because request_ids are user-provided opaque strings.
            clauses.append("r.metadata LIKE ?")
            params.append(f'%"request_id": "{request_id}"%')

        where = " AND ".join(clauses)
        sql = f"""
            SELECT
                r.id, r.started_at, r.agent_name, r.agent_version, r.kind,
                res.outcome, res.failure_type, r.metadata
            FROM runs r
            LEFT JOIN results res ON res.run_id = r.id
            WHERE {where}
            ORDER BY r.started_at DESC
        """
        rows = self.conn.execute(sql, params).fetchall()

        traces: List[dict] = []
        for row in rows:
            metadata = json.loads(row[7]) if row[7] else {}
            trace = {
                "id": row[0],
                "started_at": row[1],
                "agent_name": row[2],
                "agent_version": row[3],
                "kind": row[4],
                "outcome": row[5],
                "failure_type": row[6],
                "request_id": metadata.get("request_id"),
            }
            traces.append(trace)

        if failed_only:
            traces = [
                t for t in traces
                if t["outcome"] not in (None, "completed")
                or t["failure_type"] not in (None, "handled", "completed")
            ]
        if limit is not None:
            traces = traces[:limit]
        return traces

    def list_runs(self, limit=20, failed_only=False):
        """Return a list of recent runs with summary stats.

        Each run is a dict with: id, started_at, agent_name, total, failures.
        Sorted by started_at DESC (most recent first).
        """
        bad_types = ("crashed", "looped", "gave_up", "hallucinated",
                     "silent_wrong", "timeout")
        placeholders = ",".join("?" * len(bad_types))

        # One query, pre-aggregated. SQLite doesn't mind a moderately complex query
        # at this scale, and it avoids N+1 lookups across runs.
        sql = f"""
            SELECT
                r.id,
                r.started_at,
                r.agent_name,
                (SELECT COUNT(*) FROM results WHERE run_id = r.id) AS total,
                (SELECT COUNT(*) FROM results
                    WHERE run_id = r.id AND failure_type IN ({placeholders})) AS failures
            FROM runs r
            ORDER BY r.started_at DESC
        """
        rows = self.conn.execute(sql, bad_types).fetchall()

        results = [
            {"id": r[0], "started_at": r[1], "agent_name": r[2],
             "total": r[3], "failures": r[4]}
            for r in rows
        ]
        if failed_only:
            results = [r for r in results if r["failures"] > 0]
        if limit:
            results = results[:limit]
        return results
