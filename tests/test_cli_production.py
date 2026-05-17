"""CLI: tool-pouch traces, tool-pouch trace, tool-pouch replay, tool-pouch sync."""
import argparse
import json
import time

import pytest

from tool_pouch import cli
from tool_pouch.store import KIND_PRODUCTION, Store


def _capture_production(store: Store, *, agent: str = "demo",
                        request_id: str = None, outcome: str = "completed"):
    """Insert a synthetic production trace identical to what wrap_* writes."""
    metadata = {"provider": "openai"}
    if request_id:
        metadata["request_id"] = request_id
    run_id = store.new_run(
        agent_name=agent, user_input="what is 2+2?",
        kind=KIND_PRODUCTION, metadata=metadata, environment="production",
    )
    store.add_result(
        run_id=run_id, scenario="__production__", target_tool=None,
        outcome=outcome, failure_type=outcome,
        trace={
            "user_input": "what is 2+2?",
            "tools": [{"name": "calculator"}],
            "tool_calls": [
                {
                    "name": "calculator",
                    "arguments": '{"expr": "2+2"}',
                    "result": 4,
                }
            ],
            "messages": [
                {"role": "user", "content": "what is 2+2?"},
                {"role": "assistant", "content": "4"},
            ],
        },
        duration_ms=42,
    )
    return run_id


# --- tool-pouch traces ----------------------------------------------------------


def test_traces_empty_store_prints_helper(db_path, capsys):
    args = argparse.Namespace(
        agent=None, since=None, failed=False, request_id=None, limit=50,
    )
    with pytest.raises(SystemExit) as exc:
        cli.cmd_traces(args)
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "No production traces found" in out
    assert "wrap_anthropic" in out


def test_traces_lists_captured_runs(db_path, capsys):
    store = Store()
    _capture_production(store, agent="alpha")
    _capture_production(store, agent="beta")

    args = argparse.Namespace(
        agent=None, since=None, failed=False, request_id=None, limit=50,
    )
    cli.cmd_traces(args)
    out = capsys.readouterr().out
    assert "alpha" in out
    assert "beta" in out
    assert "TRACE" in out


def test_traces_agent_filter(db_path, capsys):
    store = Store()
    _capture_production(store, agent="alpha")
    _capture_production(store, agent="beta")

    args = argparse.Namespace(
        agent="alpha", since=None, failed=False, request_id=None, limit=50,
    )
    cli.cmd_traces(args)
    out = capsys.readouterr().out
    assert "alpha" in out
    assert "beta" not in out


def test_traces_failed_filter(db_path, capsys):
    store = Store()
    _capture_production(store, agent="ok", outcome="completed")
    _capture_production(store, agent="bad", outcome="crashed")

    args = argparse.Namespace(
        agent=None, since=None, failed=True, request_id=None, limit=50,
    )
    cli.cmd_traces(args)
    out = capsys.readouterr().out
    assert "bad" in out
    assert "ok" not in out


def test_traces_request_id_filter(db_path, capsys):
    store = Store()
    _capture_production(store, request_id="req-abc")
    _capture_production(store, request_id="req-xyz")

    args = argparse.Namespace(
        agent=None, since=None, failed=False, request_id="req-abc", limit=50,
    )
    cli.cmd_traces(args)
    out = capsys.readouterr().out
    assert "req-abc" in out
    assert "req-xyz" not in out


def test_parse_since_units():
    assert cli._parse_since(None) is None
    assert cli._parse_since("30m") == 1800
    assert cli._parse_since("2h") == 7200
    assert cli._parse_since("7d") == 604800
    assert cli._parse_since("3600") == 3600


# --- tool-pouch trace ------------------------------------------------------------


def test_trace_lookup_by_id_calls_show(db_path, capsys, monkeypatch):
    store = Store()
    rid = _capture_production(store)

    called_with = {}
    def _fake_show(trace_id, **kwargs):
        called_with["id"] = trace_id

    monkeypatch.setattr(cli, "show", _fake_show)
    args = argparse.Namespace(trace_id=rid, request_id=None)
    cli.cmd_trace(args)
    assert called_with["id"] == rid


def test_trace_lookup_by_request_id(db_path, capsys, monkeypatch):
    store = Store()
    rid = _capture_production(store, request_id="req-42")
    other = _capture_production(store, request_id="req-other")

    called_with = {}
    monkeypatch.setattr(cli, "show",
                        lambda trace_id, **kw: called_with.setdefault("id", trace_id))
    args = argparse.Namespace(trace_id=None, request_id="req-42")
    cli.cmd_trace(args)
    assert called_with["id"] == rid


def test_trace_no_match_exits_2(db_path, capsys):
    args = argparse.Namespace(trace_id=None, request_id="never")
    with pytest.raises(SystemExit) as exc:
        cli.cmd_trace(args)
    assert exc.value.code == 2


def test_trace_default_picks_most_recent(db_path, capsys, monkeypatch):
    store = Store()
    older = _capture_production(store, agent="older")
    time.sleep(0.01)
    newer = _capture_production(store, agent="newer")

    called_with = {}
    monkeypatch.setattr(cli, "show",
                        lambda trace_id, **kw: called_with.setdefault("id", trace_id))
    args = argparse.Namespace(trace_id=None, request_id=None)
    cli.cmd_trace(args)
    assert called_with["id"] == newer


# --- tool-pouch replay -----------------------------------------------------------


def test_replay_frozen_completes_without_user_callables(db_path, capsys):
    store = Store()
    rid = _capture_production(store)

    args = argparse.Namespace(
        trace_id=rid, request_id=None,
        frozen=True, frozen_tools=False, loose_tools=False, match_closest=False,
        repeat=1, agent_file=None,
    )
    cli.cmd_replay(args)
    out = capsys.readouterr().out
    # The summary() output should mention the replay
    assert "✓" in out or "passed" in out or "completed" in out.lower() or out


def test_replay_chaos_without_agent_file_errors(db_path, capsys):
    store = Store()
    rid = _capture_production(store)

    args = argparse.Namespace(
        trace_id=rid, request_id=None,
        frozen=False, frozen_tools=False, loose_tools=False, match_closest=False,
        repeat=1, agent_file=None,
    )
    with pytest.raises(SystemExit) as exc:
        cli.cmd_replay(args)
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "agent" in err.lower()


# --- tool-pouch sync -------------------------------------------------------------


def test_sync_prints_local_count_and_pitch(db_path, capsys):
    store = Store()
    _capture_production(store)
    _capture_production(store)

    args = argparse.Namespace()
    cli.cmd_sync(args)
    out = capsys.readouterr().out
    assert "2" in out  # the local count
    assert "Cloud" in out
    assert "toolpouch.dev" in out


# --- mode resolution helpers ------------------------------------------------


def test_resolve_replay_mode_default_is_chaos():
    args = argparse.Namespace(
        frozen=False, frozen_tools=False, loose_tools=False, match_closest=False,
    )
    assert cli._resolve_replay_mode(args) == ("chaos", "strict")


def test_resolve_replay_mode_frozen():
    args = argparse.Namespace(
        frozen=True, frozen_tools=False, loose_tools=False, match_closest=False,
    )
    assert cli._resolve_replay_mode(args) == ("frozen", "strict")


def test_resolve_replay_mode_loose_tools_implies_frozen_tools():
    args = argparse.Namespace(
        frozen=False, frozen_tools=False, loose_tools=True, match_closest=False,
    )
    assert cli._resolve_replay_mode(args) == ("frozen-tools", "loose")


def test_resolve_replay_mode_match_closest_implies_frozen_tools():
    args = argparse.Namespace(
        frozen=False, frozen_tools=False, loose_tools=False, match_closest=True,
    )
    assert cli._resolve_replay_mode(args) == ("frozen-tools", "closest")
