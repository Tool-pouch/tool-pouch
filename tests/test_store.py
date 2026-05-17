"""Round-trip runs/results, prefix lookup, list_runs aggregation."""
import os

import pytest

from tool_pouch.store import Store, default_db_path


def test_default_db_path_uses_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("TOOL_POUCH_DB", str(tmp_path / "custom.db"))
    assert default_db_path() == tmp_path / "custom.db"


def test_default_db_path_falls_back_to_home(monkeypatch):
    monkeypatch.delenv("TOOL_POUCH_DB", raising=False)
    assert default_db_path().name == "tool_pouch.db"
    assert ".tool-pouch" in default_db_path().parts


def test_round_trip(store):
    run_id = store.new_run("agent_a", "hi there")
    store.add_result(
        run_id, "timeout", "search", "timeout", "timeout",
        {"output": None, "error": "20s"}, 20000,
    )
    results = store.results_for(run_id)
    assert len(results) == 1
    assert results[0]["scenario"] == "timeout"
    assert results[0]["failure_type"] == "timeout"
    assert results[0]["trace"]["error"] == "20s"


def test_results_for_unknown_run_returns_empty(store):
    assert store.results_for("nope") == []


def test_prefix_lookup(store):
    run_id = store.new_run("agent_a", "hi")
    store.add_result(run_id, "s", "t", "completed", "handled", {}, 10)
    short = run_id[:8]
    assert len(store.results_for(short)) == 1


def test_latest_run_id(store):
    assert store.latest_run_id() is None
    a = store.new_run("agent_a", "x")
    b = store.new_run("agent_b", "y")
    assert store.latest_run_id() == b
    assert a != b


def test_list_runs_failed_only(store):
    passed = store.new_run("good", "x")
    store.add_result(passed, "s", "t", "completed", "handled", {}, 5)

    failed = store.new_run("bad", "y")
    store.add_result(failed, "s", "t", "crashed", "crashed", {}, 5)

    all_runs = store.list_runs()
    only_failed = store.list_runs(failed_only=True)

    assert {r["id"] for r in all_runs} == {passed, failed}
    assert {r["id"] for r in only_failed} == {failed}


def test_trace_schema_version_is_stamped(store):
    run_id = store.new_run("a", "x")
    store.add_result(run_id, "s", "t", "completed", "handled", {"output": "ok"}, 1)
    trace = store.results_for(run_id)[0]["trace"]
    assert trace["trace_schema_version"] == 1
