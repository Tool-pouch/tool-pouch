"""Verify the judge actually fans out across the runner's semaphore."""
import asyncio
import time

import pytest

from tool_pouch.runner import Runner


def real_tool(name, args):
    return {"results": [{"url": "https://example.com"}]}


async def silent_agent(user_input, tool_caller):
    """Returns text the heuristic won't catch, forcing the judge path."""
    await tool_caller("search", {"q": user_input})
    return {"output": "everything is fine", "tool_calls": []}


@pytest.mark.asyncio
async def test_judge_calls_run_concurrently(monkeypatch, store):
    """If judge calls were serial, total time would be ~sleep_s * N.

    With parallel=N inside the semaphore, total time should be ~sleep_s.
    """
    sleep_s = 0.4
    in_flight = {"now": 0, "max": 0}
    lock = asyncio.Lock()

    def slow_judge(*, user_input, scenario, tool, output, tool_calls):
        # We're inside asyncio.to_thread, so use sync sleep + sync bookkeeping
        nonlocal_lock = lock
        # We can't await here, but we can read shared state under the GIL safely
        # for this peak-counter purpose. asyncio.to_thread uses a real thread.
        in_flight["now"] += 1
        in_flight["max"] = max(in_flight["max"], in_flight["now"])
        time.sleep(sleep_s)
        in_flight["now"] -= 1
        return {"verdict": "silent_wrong", "source": "control_flow",
                "hypothesis": "test"}

    from tool_pouch import runner as runner_mod
    monkeypatch.setattr(runner_mod.llm_judge, "judge", slow_judge)

    n = 4
    runner = Runner(silent_agent, real_tool, ["search"], store=store)
    scenarios = ["null_response", "empty_response", "wrong_type",
                 "partial_data"][:n]

    start = time.time()
    await runner.run("hi", scenarios=scenarios, parallel=n)
    elapsed = time.time() - start

    # Concurrent judges: total time should be much closer to sleep_s than n*sleep_s
    assert elapsed < sleep_s * n * 0.7, (
        f"judge appears serial: elapsed={elapsed:.2f}s for {n} calls of "
        f"{sleep_s}s each (serial would be {sleep_s * n:.2f}s)"
    )
    assert in_flight["max"] >= 2, (
        f"only {in_flight['max']} judge call(s) in flight at once"
    )
