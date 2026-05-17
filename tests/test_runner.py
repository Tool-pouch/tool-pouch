"""Highest-leverage runner tests. The judge bug from the v0.1.0 review
would have been caught by `test_judge_receives_user_input_and_scenario`.
"""
import asyncio

import pytest

from tool_pouch.runner import Runner


def real_tool(name, args):
    if name == "search":
        return {"results": [{"url": "https://example.com"}]}
    return {"content": "hello"}


async def naive_agent(user_input, tool_caller):
    """Mirrors the demo: doesn't validate tool responses."""
    tool_calls = []
    search = await tool_caller("search", {"q": user_input})
    tool_calls.append({"tool": "search", "args": {"q": user_input}, "result": search})
    url = search["results"][0]["url"]
    fetched = await tool_caller("fetch", {"url": url})
    tool_calls.append({"tool": "fetch", "args": {"url": url}, "result": fetched})
    return {"output": f"got {fetched['content']}", "tool_calls": tool_calls}


async def acknowledging_agent(user_input, tool_caller):
    try:
        await tool_caller("search", {"q": user_input})
        return {"output": "ok", "tool_calls": []}
    except Exception:
        return {"output": "tool failed - unable to complete", "tool_calls": []}


@pytest.mark.asyncio
async def test_explicit_scenarios_produce_expected_row_count(store):
    runner = Runner(naive_agent, real_tool, ["search", "fetch"], store=store)
    run_id = await runner.run(
        "hi", scenarios=["server_error", "null_response"], parallel=4,
    )
    # 2 tools × 2 scenarios
    assert len(store.results_for(run_id)) == 4


@pytest.mark.asyncio
async def test_server_error_classified_as_crashed(store):
    runner = Runner(naive_agent, real_tool, ["search"], store=store)
    run_id = await runner.run("hi", scenarios=["server_error"], parallel=1)
    results = store.results_for(run_id)
    assert len(results) == 1
    assert results[0]["failure_type"] == "crashed"


@pytest.mark.asyncio
async def test_acknowledgment_heuristic_marks_handled(store):
    runner = Runner(acknowledging_agent, real_tool, ["search"], store=store)
    run_id = await runner.run("hi", scenarios=["server_error"], parallel=1)
    assert store.results_for(run_id)[0]["failure_type"] == "handled"


@pytest.mark.asyncio
async def test_timeout_is_respected(store):
    runner = Runner(naive_agent, real_tool, ["search"], store=store,
                    agent_timeout_s=0.5)
    run_id = await runner.run("hi", scenarios=["timeout"], parallel=1)
    result = store.results_for(run_id)[0]
    assert result["failure_type"] == "timeout"
    assert "0.5s" in result["trace"]["error"]


@pytest.mark.asyncio
async def test_judge_receives_user_input_and_scenario(monkeypatch, store):
    """Regression: the judge was previously called with two empty strings."""
    captured = {}

    def fake_judge(*, user_input, scenario, tool, output, tool_calls):
        captured.update(user_input=user_input, scenario=scenario, tool=tool)
        return {"verdict": "silent_wrong", "source": "control_flow",
                "hypothesis": "test"}

    from tool_pouch import runner as runner_mod
    monkeypatch.setattr(runner_mod.llm_judge, "judge", fake_judge)

    async def silent_agent(user_input, tool_caller):
        await tool_caller("search", {"q": user_input})
        return {"output": "everything is fine", "tool_calls": []}

    runner = Runner(silent_agent, real_tool, ["search"], store=store)
    await runner.run("best pizza in NYC", scenarios=["null_response"],
                     parallel=1)

    assert captured["user_input"] == "best pizza in NYC"
    assert captured["scenario"] == "null_response"
    assert captured["tool"] == "search"
