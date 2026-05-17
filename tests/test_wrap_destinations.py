"""Destination contract: LocalStore, JSONLogger, HTTPSink."""
import io
import json
import time

import pytest

from tool_pouch.store import KIND_PRODUCTION, Store
from tool_pouch.wrap.destinations import (
    Destination,
    HTTPSink,
    JSONLogger,
    LocalStore,
    TraceRecord,
)


def _record(**overrides) -> TraceRecord:
    base = dict(
        run_id="run-1",
        started_at=1000.0,
        agent_name="agent_a",
        agent_version="0.1.0",
        request_id="req-abc",
        metadata={"env": "prod"},
        scenario="__production__",
        target_tool=None,
        outcome="completed",
        failure_type="completed",
        trace={"user_input": "hello", "messages": []},
        duration_ms=42,
        kind=KIND_PRODUCTION,
    )
    base.update(overrides)
    return TraceRecord(**base)


def test_localstore_writes_run_and_result(db_path):
    store = Store()
    sink = LocalStore(store=store)
    sink.write(_record())

    rows = store.list_traces(limit=10)
    assert len(rows) == 1
    assert rows[0]["agent_name"] == "agent_a"
    assert rows[0]["kind"] == "production"
    assert rows[0]["request_id"] == "req-abc"


def test_localstore_lazy_default_store(db_path):
    sink = LocalStore()
    sink.write(_record(agent_name="lazy_default"))

    store = Store()
    rows = store.list_traces(limit=10)
    assert any(r["agent_name"] == "lazy_default" for r in rows)


def test_jsonlogger_writes_ndjson_lines():
    buf = io.StringIO()
    sink = JSONLogger(stream=buf)
    sink.write(_record())
    sink.write(_record(run_id="run-2"))

    lines = buf.getvalue().strip().split("\n")
    assert len(lines) == 2
    assert json.loads(lines[0])["run_id"] == "run-1"
    assert json.loads(lines[1])["run_id"] == "run-2"


def test_jsonlogger_rejects_unknown_format():
    with pytest.raises(ValueError):
        JSONLogger(format="protobuf")


def test_destination_protocol_runtime_check():
    """LocalStore + JSONLogger + HTTPSink all satisfy the Protocol."""
    assert isinstance(LocalStore(), Destination)
    assert isinstance(JSONLogger(stream=io.StringIO()), Destination)
    assert isinstance(HTTPSink(url="https://example.invalid/traces"), Destination)


def test_httpsink_batches_until_size_reached(monkeypatch):
    posted = []

    def fake_post(self, items):
        posted.append(list(items))

    monkeypatch.setattr(HTTPSink, "_post", fake_post)
    sink = HTTPSink(url="https://example.invalid", batch_size=3, flush_interval_s=999)

    sink.write(_record(run_id="r1"))
    sink.write(_record(run_id="r2"))
    assert posted == []

    sink.write(_record(run_id="r3"))
    assert len(posted) == 1
    assert [item["run_id"] for item in posted[0]] == ["r1", "r2", "r3"]


def test_httpsink_close_flushes_remaining(monkeypatch):
    posted = []
    monkeypatch.setattr(HTTPSink, "_post", lambda self, items: posted.append(list(items)))

    sink = HTTPSink(url="https://example.invalid", batch_size=999, flush_interval_s=999)
    sink.write(_record(run_id="r1"))
    sink.close()
    assert posted == [[_record(run_id="r1").to_dict()]]


def test_httpsink_flushes_on_interval(monkeypatch):
    posted = []
    monkeypatch.setattr(HTTPSink, "_post", lambda self, items: posted.append(list(items)))

    sink = HTTPSink(url="https://example.invalid", batch_size=999, flush_interval_s=0.0)
    sink.write(_record(run_id="r1"))
    # flush_interval_s=0 means every write should trigger a flush.
    assert len(posted) == 1


def test_httpsink_drops_after_max_retries(monkeypatch):
    """Network errors are swallowed silently after retries exhausted."""
    monkeypatch.setattr(time, "sleep", lambda *_: None)

    def boom(*args, **kwargs):
        raise ConnectionError("boom")

    monkeypatch.setattr("urllib.request.urlopen", boom)
    sink = HTTPSink(url="https://example.invalid", batch_size=1, max_retries=2)
    sink.write(_record(run_id="r1"))
    # Did not raise — that's the contract.
