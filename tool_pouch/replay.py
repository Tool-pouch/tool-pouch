"""Replay captured production traces against scenarios or real flows.

The CLI surface is `tool-pouch replay <trace_id> [--mode ...] [--repeat N]`.
This module provides the building blocks that command uses; library
callers can use them directly to script bespoke replay flows.

Modes
-----

frozen
    Deterministic walk-through. Doesn't call the model or tools — just
    re-emits the captured output and tool calls. Used for "what
    happened" review.

frozen-tools (strict | loose | closest)
    Re-call the model with the captured input + tools, but stub tool
    calls with their captured results. The matching strategy decides
    what happens when the new tool call doesn't exactly match a
    captured one:

        strict      raise — useful for regression testing
        loose       match by tool name only
        closest     nearest-neighbor by argument keys

chaos (default)
    Re-call everything: real model, real tools. Pair with the existing
    Tool Pouch scenario injection (timeout, malformed JSON, etc.) for
    "would my agent have crashed?" answers.

repeat N
    Run a chaos replay N times and report verdict percentages per
    (tool, scenario) cell. Useful for surfacing flaky failure rates.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple


ToolFn = Callable[[str, Dict[str, Any]], Awaitable[Any]]
AgentFn = Callable[[str, ToolFn], Awaitable[Dict[str, Any]]]


VALID_MODES = ("frozen", "frozen-tools", "chaos")
VALID_MATCH_STRATEGIES = ("strict", "loose", "closest")


# --- public API --------------------------------------------------------------


@dataclass
class ReplayInputs:
    """Everything the existing Runner needs to replay one trace."""

    user_input: str
    tools: List[str]
    agent_fn: AgentFn
    real_tool_fn: ToolFn


def build_replay_inputs(
    trace: Dict[str, Any],
    mode: str = "chaos",
    match: str = "strict",
    user_agent_fn: Optional[AgentFn] = None,
    user_tool_fn: Optional[ToolFn] = None,
) -> ReplayInputs:
    """Construct the four runner inputs for a chosen replay mode.

    Parameters
    ----------
    trace
        Captured production trace (from `tool_pouch.wrap_*()`). Must contain
        `user_input`, `tools`, and `tool_calls` at minimum.
    mode
        'frozen' | 'frozen-tools' | 'chaos'.
    match
        For frozen-tools: 'strict' | 'loose' | 'closest'.
    user_agent_fn
        Required for 'frozen-tools' and 'chaos'. The user's real
        agent_fn — Tool Pouch routes the model call through this.
    user_tool_fn
        Required for 'chaos'. The user's real tool implementation.
    """
    if mode not in VALID_MODES:
        raise ValueError(
            f"mode must be one of {VALID_MODES}, got {mode!r}"
        )
    if match not in VALID_MATCH_STRATEGIES:
        raise ValueError(
            f"match must be one of {VALID_MATCH_STRATEGIES}, got {match!r}"
        )

    user_input = trace.get("user_input") or ""
    tools = _extract_tool_names(trace)

    if mode == "frozen":
        return ReplayInputs(
            user_input=user_input,
            tools=tools,
            agent_fn=_make_frozen_agent_fn(trace),
            real_tool_fn=_make_frozen_tool_fn(trace, match="strict"),
        )

    if mode == "frozen-tools":
        if user_agent_fn is None:
            raise ValueError(
                "frozen-tools mode requires user_agent_fn — pass your "
                "real agent_fn so Tool Pouch can re-call the model."
            )
        return ReplayInputs(
            user_input=user_input,
            tools=tools,
            agent_fn=user_agent_fn,
            real_tool_fn=_make_frozen_tool_fn(trace, match=match),
        )

    if user_agent_fn is None or user_tool_fn is None:
        raise ValueError(
            "chaos mode requires both user_agent_fn and user_tool_fn — "
            "pass your real agent_fn and tool implementation."
        )
    return ReplayInputs(
        user_input=user_input,
        tools=tools,
        agent_fn=user_agent_fn,
        real_tool_fn=user_tool_fn,
    )


# --- frozen agent (replays captured walk) ------------------------------------


def _make_frozen_agent_fn(trace: Dict[str, Any]) -> AgentFn:
    """An agent_fn that re-emits the captured trace's output + tool_calls.

    Doesn't call the model or invoke tool_caller. Useful for surfacing
    "this is exactly what happened" without spending tokens or hitting
    side-effecting tools.
    """
    captured_output = _captured_output(trace)
    captured_tool_calls = _captured_tool_calls(trace)

    async def _agent_fn(user_input: str, tool_caller: ToolFn) -> Dict[str, Any]:
        return {
            "output": captured_output,
            "tool_calls": captured_tool_calls,
        }

    return _agent_fn


# --- frozen tool lookup ------------------------------------------------------


def _make_frozen_tool_fn(trace: Dict[str, Any], match: str) -> ToolFn:
    """Returns a tool_fn that looks up captured tool outputs by (name, args).

    The lookup table is built once from the trace's `tool_calls` and
    indexed by name. Match strategy decides how to find a hit when
    args don't exactly equal the captured args.
    """
    by_name: Dict[str, List[Tuple[Dict[str, Any], Any]]] = {}
    for tc in trace.get("tool_calls") or []:
        name = tc.get("name") or tc.get("tool")
        if not name:
            continue
        args = _normalize_args(tc.get("arguments"))
        result = tc.get("result")
        by_name.setdefault(name, []).append((args, result))

    async def _tool_fn(name: str, args: Dict[str, Any]) -> Any:
        candidates = by_name.get(name, [])
        if not candidates:
            raise ReplayMissError(
                f"replay miss: no captured calls for tool {name!r}"
            )
        normalized_args = _normalize_args(args)

        if match == "strict":
            for cand_args, result in candidates:
                if cand_args == normalized_args:
                    return result
            raise ReplayMissError(
                f"replay miss: tool {name!r} called with {normalized_args!r}, "
                f"captured args were {[c[0] for c in candidates]!r}"
            )

        if match == "loose":
            return candidates[0][1]

        # closest: nearest neighbor by jaccard overlap of arg key/value pairs
        best_score = -1.0
        best_result = candidates[0][1]
        for cand_args, result in candidates:
            score = _jaccard(normalized_args, cand_args)
            if score > best_score:
                best_score = score
                best_result = result
        return best_result

    return _tool_fn


class ReplayMissError(LookupError):
    """Raised when frozen-tools strict mode can't find a captured match."""


# --- helpers -----------------------------------------------------------------


def _extract_tool_names(trace: Dict[str, Any]) -> List[str]:
    """Pull tool names from either the captured `tools` schema or `tool_calls`.

    Falls back to tool_call names if the schema array is absent.
    """
    names: List[str] = []
    for t in trace.get("tools") or []:
        if isinstance(t, dict):
            n = t.get("name") or (t.get("function") or {}).get("name")
            if n:
                names.append(n)
        elif isinstance(t, str):
            names.append(t)

    if not names:
        for tc in trace.get("tool_calls") or []:
            n = tc.get("name") or tc.get("tool")
            if n and n not in names:
                names.append(n)

    return names


def _captured_output(trace: Dict[str, Any]) -> Optional[str]:
    """Return the assistant's last text output from the trace."""
    if "output" in trace and trace["output"] is not None:
        return trace["output"]
    for msg in reversed(trace.get("messages") or []):
        role = msg.get("role") if isinstance(msg, dict) else None
        if role == "assistant":
            content = msg.get("content")
            if isinstance(content, str):
                return content
    return None


def _captured_tool_calls(trace: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Normalize tool_calls into the {tool, args, result} runner shape."""
    out = []
    for tc in trace.get("tool_calls") or []:
        out.append({
            "tool": tc.get("name") or tc.get("tool"),
            "args": _normalize_args(tc.get("arguments") or tc.get("args")),
            "result": tc.get("result"),
        })
    return out


def _normalize_args(args: Any) -> Dict[str, Any]:
    """Tool args may be JSON strings (OpenAI) or dicts (Anthropic)."""
    if args is None:
        return {}
    if isinstance(args, str):
        try:
            parsed = json.loads(args)
            return parsed if isinstance(parsed, dict) else {"__value": parsed}
        except (TypeError, ValueError):
            return {"__value": args}
    if isinstance(args, dict):
        return dict(args)
    return {"__value": args}


def _jaccard(a: Dict[str, Any], b: Dict[str, Any]) -> float:
    """Set-similarity over (key, value) pairs. 0..1."""
    pa = {(k, _hashable(v)) for k, v in a.items()}
    pb = {(k, _hashable(v)) for k, v in b.items()}
    if not pa and not pb:
        return 1.0
    return len(pa & pb) / max(1, len(pa | pb))


def _hashable(value: Any) -> Any:
    if isinstance(value, (list, dict)):
        return json.dumps(value, sort_keys=True, default=str)
    return value


# --- repeat aggregation ------------------------------------------------------


def aggregate_verdicts(
    runs: List[Dict[Tuple[str, str], str]],
) -> Dict[Tuple[str, str], Dict[str, float]]:
    """Compute verdict percentages per (tool, scenario) across N replays.

    Input: list of {(tool, scenario): verdict} dicts, one per replay.
    Output: {(tool, scenario): {verdict: pct, ...}}.

    Used by the `tool-pouch replay --repeat N` reporter to show "this tool
    fails 14% of the time on the malformed_json scenario."
    """
    counts: Dict[Tuple[str, str], Dict[str, int]] = {}
    totals: Dict[Tuple[str, str], int] = {}
    for run in runs:
        for cell, verdict in run.items():
            counts.setdefault(cell, {})[verdict] = (
                counts.setdefault(cell, {}).get(verdict, 0) + 1
            )
            totals[cell] = totals.get(cell, 0) + 1

    out: Dict[Tuple[str, str], Dict[str, float]] = {}
    for cell, vmap in counts.items():
        total = totals[cell] or 1
        out[cell] = {v: c / total for v, c in vmap.items()}
    return out
