"""Retention pruning targets only kind='production' rows."""
import time


def _back_date(store, run_id, days_ago):
    """Cheat the started_at column so we can test pruning thresholds."""
    cutoff = time.time() - (days_ago * 86400)
    store.conn.execute("UPDATE runs SET started_at = ? WHERE id = ?", (cutoff, run_id))
    store.conn.commit()


def test_prune_keeps_recent_production(store):
    fresh = store.new_run("agent", "x", kind="production")
    store.add_result(fresh, "__production__", None, "completed", "completed", {}, 5)
    _back_date(store, fresh, days_ago=2)

    pruned = store.prune_old_traces(days=30)
    assert pruned == 0
    assert store.results_for(fresh)


def test_prune_drops_old_production_with_results(store):
    old = store.new_run("agent", "x", kind="production")
    store.add_result(old, "__production__", None, "completed", "completed", {}, 5)
    _back_date(store, old, days_ago=45)

    pruned = store.prune_old_traces(days=30)
    assert pruned == 1
    assert store.results_for(old) == []


def test_prune_leaves_test_runs_alone(store):
    test_run = store.new_run("agent", "x", kind="test")
    store.add_result(test_run, "scenario_a", "tool_b", "completed", "handled", {}, 5)
    _back_date(store, test_run, days_ago=999)

    pruned = store.prune_old_traces(days=30)
    assert pruned == 0
    assert len(store.results_for(test_run)) == 1


def test_prune_leaves_replay_runs_alone(store):
    replay_run = store.new_run("agent", "x", kind="replay")
    store.add_result(replay_run, "timeout", "search", "completed", "handled", {}, 5)
    _back_date(store, replay_run, days_ago=365)

    pruned = store.prune_old_traces(days=30)
    assert pruned == 0
    assert len(store.results_for(replay_run)) == 1


def test_prune_zero_days_is_noop(store):
    rid = store.new_run("agent", "x", kind="production")
    store.add_result(rid, "__production__", None, "completed", "completed", {}, 5)
    _back_date(store, rid, days_ago=999)

    assert store.prune_old_traces(days=0) == 0
    assert store.results_for(rid)
