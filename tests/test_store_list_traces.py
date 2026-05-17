"""Filters on Store.list_traces: kind, agent_name, since, request_id, failed_only."""
import time


def test_list_traces_returns_only_production_by_default(store):
    prod = store.new_run("agent_a", "x", kind="production")
    store.add_result(prod, "__production__", None, "completed", "completed", {}, 1)

    test_run = store.new_run("agent_a", "x", kind="test")
    store.add_result(test_run, "timeout", "search", "completed", "handled", {}, 1)

    rows = store.list_traces()
    assert {r["id"] for r in rows} == {prod}
    assert rows[0]["kind"] == "production"


def test_list_traces_agent_name_filter(store):
    a = store.new_run("alpha", "x", kind="production")
    store.add_result(a, "__production__", None, "completed", "completed", {}, 1)
    b = store.new_run("beta", "x", kind="production")
    store.add_result(b, "__production__", None, "completed", "completed", {}, 1)

    only_alpha = store.list_traces(agent_name="alpha")
    assert {r["id"] for r in only_alpha} == {a}


def test_list_traces_since_filter(store):
    old = store.new_run("agent", "x", kind="production")
    store.add_result(old, "__production__", None, "completed", "completed", {}, 1)
    store.conn.execute(
        "UPDATE runs SET started_at = ? WHERE id = ?",
        (time.time() - 7200, old),
    )
    store.conn.commit()

    fresh = store.new_run("agent", "x", kind="production")
    store.add_result(fresh, "__production__", None, "completed", "completed", {}, 1)

    last_hour = store.list_traces(since_seconds=3600)
    assert {r["id"] for r in last_hour} == {fresh}


def test_list_traces_failed_only_filter(store):
    ok = store.new_run("agent", "x", kind="production")
    store.add_result(ok, "__production__", None, "completed", "completed", {}, 1)

    bad = store.new_run("agent", "x", kind="production")
    store.add_result(bad, "__production__", None, "crashed", "crashed", {}, 1)

    failed = store.list_traces(failed_only=True)
    assert {r["id"] for r in failed} == {bad}


def test_list_traces_request_id_match(store):
    rid_a = store.new_run(
        "agent", "x", kind="production",
        metadata={"request_id": "req-123"},
    )
    store.add_result(rid_a, "__production__", None, "completed", "completed", {}, 1)

    rid_b = store.new_run(
        "agent", "x", kind="production",
        metadata={"request_id": "req-999"},
    )
    store.add_result(rid_b, "__production__", None, "completed", "completed", {}, 1)

    matches = store.list_traces(request_id="req-123")
    assert {r["id"] for r in matches} == {rid_a}
    assert matches[0]["request_id"] == "req-123"


def test_list_traces_limit(store):
    for _ in range(5):
        rid = store.new_run("agent", "x", kind="production")
        store.add_result(rid, "__production__", None, "completed", "completed", {}, 1)

    assert len(store.list_traces(limit=2)) == 2
    assert len(store.list_traces(limit=None)) == 5
