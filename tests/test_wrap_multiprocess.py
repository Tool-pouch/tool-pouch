"""Multi-process safety: 4 fork() workers x 100 traces each → all 400 land.

Validates the os.register_at_fork hook + WAL-mode SQLite write semantics
under the gunicorn/uvicorn pre-fork model that real services use.

macOS fork() requires `OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES` in some
environments. We use multiprocessing.set_start_method('fork') explicitly
so we're testing the fork path, not spawn (which doesn't trigger
register_at_fork at all).
"""
import multiprocessing as mp
import os
import sys
import time
from pathlib import Path

import pytest

import tool_pouch
from tool_pouch.store import KIND_PRODUCTION, Store
from tool_pouch.wrap.destinations import LocalStore, TraceRecord
from tool_pouch.wrap.writer import _WriterThread, set_destinations


def _worker(db_path: str, label: str, count: int) -> None:
    os.environ["TOOL_POUCH_DB"] = db_path

    writer = _WriterThread()
    writer.add_destination(LocalStore())

    for i in range(count):
        writer.enqueue(
            TraceRecord(
                run_id=f"{label}-{i}",
                started_at=time.time(),
                agent_name=f"worker-{label}",
                agent_version=None,
                request_id=None,
                metadata={},
                scenario="__production__",
                target_tool=None,
                outcome="completed",
                failure_type="completed",
                trace={"user_input": f"{label}-{i}"},
                duration_ms=1,
                kind=KIND_PRODUCTION,
            )
        )
    writer.flush(timeout=10.0)


@pytest.mark.skipif(
    sys.platform.startswith("win"),
    reason="fork start method unavailable on Windows",
)
def test_fork_workers_all_traces_land(tmp_path, monkeypatch):
    db = tmp_path / "tool_pouch.db"
    monkeypatch.setenv("TOOL_POUCH_DB", str(db))

    # Drain the global writer's queue from any prior test, then clear
    # destinations so the parent process doesn't write to this test's DB.
    tool_pouch.flush(timeout=2.0)
    set_destinations([])

    Store()  # ensure schema exists before workers fork

    ctx = mp.get_context("fork")
    workers = []
    per_worker = 100
    labels = ["a", "b", "c", "d"]

    for label in labels:
        p = ctx.Process(target=_worker, args=(str(db), label, per_worker))
        p.start()
        workers.append(p)

    for p in workers:
        p.join(timeout=20)
        assert p.exitcode == 0, f"worker exited with {p.exitcode}"

    store = Store()
    rows = store.list_traces(kind=KIND_PRODUCTION, limit=None)
    assert len(rows) == per_worker * len(labels), (
        f"got {len(rows)}, expected {per_worker * len(labels)}"
    )

    by_agent = {}
    for r in rows:
        by_agent[r["agent_name"]] = by_agent.get(r["agent_name"], 0) + 1
    assert by_agent == {f"worker-{c}": per_worker for c in labels}
