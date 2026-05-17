"""Adapter end-to-end tests with stubbed clients.

We don't hit OpenAI or Anthropic. Stub responses verify that the adapter:
- Generates the right tool schemas
- Drives the loop
- Routes tool calls through the proxy so failure injection still works
- Captures tool_calls in the trace
"""
from __future__ import annotations

import json
from typing import Any, List

import pytest


def search(q: str) -> dict:
    """Search."""
    return {"results": [{"url": "https://example.com"}]}


def fetch(url: str) -> dict:
    """Fetch."""
    return {"content": "hello"}


# ---------- OpenAI stub ----------

class _OAFunction:
    def __init__(self, name: str, args: dict):
        self.name = name
        self.arguments = json.dumps(args)


class _OAToolCall:
    def __init__(self, name: str, args: dict, idx: int):
        self.id = f"call_{idx}"
        self.type = "function"
        self.function = _OAFunction(name, args)


class _OAMessage:
    def __init__(self, content: str | None, tool_calls: List[_OAToolCall] | None):
        self.role = "assistant"
        self.content = content
        self.tool_calls = tool_calls

    def model_dump(self, exclude_none=True):
        d = {"role": self.role, "content": self.content}
        if self.tool_calls:
            d["tool_calls"] = [
                {"id": t.id, "type": "function",
                 "function": {"name": t.function.name,
                              "arguments": t.function.arguments}}
                for t in self.tool_calls
            ]
        return {k: v for k, v in d.items() if v is not None}


class _OAChoice:
    def __init__(self, msg):
        self.message = msg


class _OAResponse:
    def __init__(self, msg):
        self.choices = [_OAChoice(msg)]


class StubOpenAI:
    """Two-turn stub: first turn calls search, second turn returns text."""

    def __init__(self):
        self.calls = 0
        self.chat = self
        self.completions = self

    def create(self, **kwargs):
        self.calls += 1
        if self.calls == 1:
            return _OAResponse(_OAMessage(
                content=None,
                tool_calls=[_OAToolCall("search", {"q": "pizza"}, 0)],
            ))
        return _OAResponse(_OAMessage(content="found pizza", tool_calls=None))


def test_openai_adapter_runs_tool_loop(db_path, monkeypatch):
    monkeypatch.setenv("AGENT_SIM_JUDGE_PROVIDER", "none")  # force fallback
    from tool_pouch import test_openai
    from tool_pouch.store import Store

    run_ids = test_openai(
        client=StubOpenAI(),
        model="gpt-4o",
        tools=[search, fetch],
        test_inputs=["pizza"],
        scenarios=["server_error"],
        parallel=2,
    )
    assert len(run_ids) == 1
    results = Store().results_for(run_ids[0])
    # 2 tools × 1 scenario
    assert len(results) == 2

    by_tool = {r["target_tool"]: r for r in results}

    # search: failure injected -> adapter catches the exception and reports
    # it back to the model. The model still produces an answer on turn 2,
    # so the outcome is "completed" and the error lives in tool_calls.
    search_result = by_tool["search"]
    assert search_result["outcome"] == "completed"
    search_calls = search_result["trace"]["tool_calls"]
    assert any("500" in (c.get("error") or "") for c in search_calls)

    # fetch: the stub model never invokes fetch, so injection is a no-op
    # and the loop completes cleanly.
    assert by_tool["fetch"]["outcome"] == "completed"


# ---------- Anthropic stub ----------

class _AnTextBlock:
    def __init__(self, text: str):
        self.type = "text"
        self.text = text

    def model_dump(self, exclude_none=True):
        return {"type": "text", "text": self.text}


class _AnToolUseBlock:
    def __init__(self, name: str, input_: dict, idx: int):
        self.type = "tool_use"
        self.id = f"tu_{idx}"
        self.name = name
        self.input = input_

    def model_dump(self, exclude_none=True):
        return {"type": "tool_use", "id": self.id,
                "name": self.name, "input": self.input}


class _AnResponse:
    def __init__(self, content: List[Any], stop_reason: str):
        self.content = content
        self.stop_reason = stop_reason


class StubAnthropic:
    def __init__(self):
        self.calls = 0
        self.messages = self

    def create(self, **kwargs):
        self.calls += 1
        if self.calls == 1:
            return _AnResponse(
                content=[_AnToolUseBlock("search", {"q": "pizza"}, 0)],
                stop_reason="tool_use",
            )
        return _AnResponse(
            content=[_AnTextBlock("found pizza")],
            stop_reason="end_turn",
        )


def test_anthropic_adapter_runs_tool_loop(db_path, monkeypatch):
    monkeypatch.setenv("AGENT_SIM_JUDGE_PROVIDER", "none")
    from tool_pouch import test_anthropic
    from tool_pouch.store import Store

    run_ids = test_anthropic(
        client=StubAnthropic(),
        model="claude-opus-4-7",
        tools=[search, fetch],
        test_inputs=["pizza"],
        scenarios=["null_response"],
        parallel=2,
    )
    assert len(run_ids) == 1
    results = Store().results_for(run_ids[0])
    assert len(results) == 2

    by_tool = {r["target_tool"]: r for r in results}

    # null_response returns None from the proxy -- the adapter passes that
    # to the model as the tool result and the loop continues.
    search_result = by_tool["search"]
    assert search_result["outcome"] == "completed"
    search_calls = search_result["trace"]["tool_calls"]
    assert search_calls and search_calls[0]["tool"] == "search"
    assert search_calls[0]["result"] is None
