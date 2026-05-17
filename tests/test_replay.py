"""Replay modes and aggregation."""
import asyncio
import json

import pytest

from tool_pouch.replay import (
    ReplayMissError,
    aggregate_verdicts,
    build_replay_inputs,
)


# --- fixtures: realistic captured trace --------------------------------------


def _trace(extra_tool_calls=None):
    base = {
        "user_input": "what's the weather in NYC?",
        "tools": [
            {"name": "search", "description": "..."},
            {"name": "calculator", "description": "..."},
        ],
        "tool_calls": [
            {
                "name": "search",
                "arguments": '{"q": "NYC weather"}',
                "result": {"temp": 72, "summary": "sunny"},
            }
        ],
        "messages": [
            {"role": "user", "content": "what's the weather in NYC?"},
            {"role": "assistant", "content": "It's 72 and sunny in NYC."},
        ],
    }
    if extra_tool_calls:
        base["tool_calls"].extend(extra_tool_calls)
    return base


# --- frozen mode ------------------------------------------------------------


async def test_frozen_replays_captured_output_without_calls():
    trace = _trace()
    inputs = build_replay_inputs(trace, mode="frozen")

    async def _no_tool(name, args):
        raise AssertionError("frozen mode must not call tool_caller")

    out = await inputs.agent_fn(inputs.user_input, _no_tool)
    assert out["output"] == "It's 72 and sunny in NYC."
    assert len(out["tool_calls"]) == 1
    assert out["tool_calls"][0]["tool"] == "search"
    assert out["tool_calls"][0]["args"] == {"q": "NYC weather"}


async def test_frozen_tools_extracted_from_schema():
    trace = _trace()
    inputs = build_replay_inputs(trace, mode="frozen")
    assert inputs.tools == ["search", "calculator"]


async def test_frozen_user_input_preserved():
    trace = _trace()
    inputs = build_replay_inputs(trace, mode="frozen")
    assert inputs.user_input == "what's the weather in NYC?"


# --- frozen-tools strict ----------------------------------------------------


async def test_frozen_tools_strict_returns_captured_when_args_match():
    trace = _trace()

    async def real_agent(user_input, tool_caller):
        return {"output": "...", "tool_calls": []}

    inputs = build_replay_inputs(
        trace, mode="frozen-tools", match="strict",
        user_agent_fn=real_agent,
    )
    result = await inputs.real_tool_fn("search", {"q": "NYC weather"})
    assert result == {"temp": 72, "summary": "sunny"}


async def test_frozen_tools_strict_raises_on_mismatch():
    trace = _trace()

    async def real_agent(user_input, tool_caller):
        return {"output": "", "tool_calls": []}

    inputs = build_replay_inputs(
        trace, mode="frozen-tools", match="strict",
        user_agent_fn=real_agent,
    )
    with pytest.raises(ReplayMissError):
        await inputs.real_tool_fn("search", {"q": "Paris weather"})


async def test_frozen_tools_strict_raises_on_unknown_tool():
    trace = _trace()
    inputs = build_replay_inputs(
        trace, mode="frozen-tools",
        user_agent_fn=lambda *a, **k: None,
    )
    with pytest.raises(ReplayMissError):
        await inputs.real_tool_fn("never_called", {})


# --- frozen-tools loose ------------------------------------------------------


async def test_frozen_tools_loose_matches_by_name_only():
    trace = _trace()
    inputs = build_replay_inputs(
        trace, mode="frozen-tools", match="loose",
        user_agent_fn=lambda *a, **k: None,
    )
    result = await inputs.real_tool_fn("search", {"q": "totally different"})
    assert result == {"temp": 72, "summary": "sunny"}


# --- frozen-tools closest ----------------------------------------------------


async def test_frozen_tools_closest_picks_best_overlap():
    trace = _trace(extra_tool_calls=[
        {
            "name": "search",
            "arguments": '{"q": "London weather", "lang": "en"}',
            "result": "london-result",
        },
        {
            "name": "search",
            "arguments": '{"q": "Tokyo weather"}',
            "result": "tokyo-result",
        },
    ])
    inputs = build_replay_inputs(
        trace, mode="frozen-tools", match="closest",
        user_agent_fn=lambda *a, **k: None,
    )
    result = await inputs.real_tool_fn(
        "search", {"q": "Tokyo weather", "lang": "en"}
    )
    # Tokyo result has q=Tokyo overlap; London has lang=en overlap.
    # Tokyo's match has both q+lang overlap if lang exists in candidate
    # — none do; so best is the candidate with q="Tokyo weather" alone.
    assert result == "tokyo-result"


# --- chaos -------------------------------------------------------------------


async def test_chaos_uses_user_supplied_callables():
    trace = _trace()

    async def real_agent(user_input, tool_caller):
        result = await tool_caller("search", {"q": "real"})
        return {"output": "real output", "tool_calls": [{"tool": "search"}]}

    async def real_tool(name, args):
        return {"real": True}

    inputs = build_replay_inputs(
        trace, mode="chaos",
        user_agent_fn=real_agent,
        user_tool_fn=real_tool,
    )
    out = await inputs.agent_fn(inputs.user_input, inputs.real_tool_fn)
    assert out["output"] == "real output"


async def test_chaos_requires_both_callables():
    trace = _trace()
    with pytest.raises(ValueError):
        build_replay_inputs(trace, mode="chaos", user_agent_fn=None)


async def test_frozen_tools_requires_user_agent_fn():
    trace = _trace()
    with pytest.raises(ValueError):
        build_replay_inputs(trace, mode="frozen-tools", user_agent_fn=None)


# --- input validation -------------------------------------------------------


def test_invalid_mode_rejected():
    with pytest.raises(ValueError):
        build_replay_inputs(_trace(), mode="dream")


def test_invalid_match_rejected():
    with pytest.raises(ValueError):
        build_replay_inputs(_trace(), mode="frozen", match="fuzzy")


# --- aggregate verdicts -----------------------------------------------------


def test_aggregate_verdicts_percentages():
    runs = [
        {("search", "timeout"): "handled", ("search", "malformed"): "crashed"},
        {("search", "timeout"): "handled", ("search", "malformed"): "handled"},
        {("search", "timeout"): "crashed", ("search", "malformed"): "handled"},
        {("search", "timeout"): "handled", ("search", "malformed"): "crashed"},
    ]
    out = aggregate_verdicts(runs)
    assert out[("search", "timeout")]["handled"] == 0.75
    assert out[("search", "timeout")]["crashed"] == 0.25
    assert out[("search", "malformed")]["handled"] == 0.5
    assert out[("search", "malformed")]["crashed"] == 0.5


def test_aggregate_verdicts_handles_missing_cells():
    runs = [
        {("a", "x"): "good"},
        {("a", "x"): "bad", ("b", "y"): "good"},
    ]
    out = aggregate_verdicts(runs)
    assert out[("a", "x")] == {"good": 0.5, "bad": 0.5}
    assert out[("b", "y")] == {"good": 1.0}
