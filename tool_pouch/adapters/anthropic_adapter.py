"""Anthropic Messages API tool-use adapter.

Mirrors the OpenAI adapter so users can swap providers without changing
the rest of their tool-pouch wiring.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, List, Optional

from tool_pouch._introspect import to_anthropic
from tool_pouch.adapters._common import build_dispatcher
from tool_pouch.adapters._judge_default import default_judge_to
from tool_pouch.runner import DEFAULT_AGENT_TIMEOUT_S


DEFAULT_SYSTEM_PROMPT = "You are a helpful assistant. Use the provided tools when relevant."
DEFAULT_MAX_TOKENS = 1024
MAX_AGENT_TURNS = 10


def test_anthropic(
    *,
    client: Any,
    model: str,
    tools: List[Any],
    test_inputs: List[str],
    system: str = DEFAULT_SYSTEM_PROMPT,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    scenarios: Optional[List[str]] = None,
    parallel: int = 8,
    agent_timeout_s: int = DEFAULT_AGENT_TIMEOUT_S,
    agent_name: str = "anthropic_agent",
    on_progress: Optional[Any] = None,
) -> List[str]:
    """Stress-test an Anthropic tool-use agent. Returns a list of run_ids."""
    from tool_pouch import stress_test

    default_judge_to("anthropic")

    dispatcher, specs = build_dispatcher(tools)
    tool_defs = [to_anthropic(s) for s in specs]

    async def agent_fn(user_input: str, tool_caller):
        messages: List[dict] = [{"role": "user", "content": user_input}]
        tool_calls_log: List[dict] = []

        for _ in range(MAX_AGENT_TURNS):
            response = await asyncio.to_thread(
                client.messages.create,
                model=model,
                system=system,
                tools=tool_defs,
                messages=messages,
                max_tokens=max_tokens,
            )

            messages.append({
                "role": "assistant",
                "content": [b.model_dump(exclude_none=True) for b in response.content],
            })

            if response.stop_reason != "tool_use":
                text = "".join(b.text for b in response.content if b.type == "text")
                return {"output": text, "tool_calls": tool_calls_log}

            tool_results = []
            for block in response.content:
                if block.type != "tool_use":
                    continue
                try:
                    result = await tool_caller(block.name, dict(block.input))
                    error = None
                except Exception as e:
                    result = None
                    error = f"{type(e).__name__}: {e}"

                tool_calls_log.append({
                    "tool": block.name,
                    "args": dict(block.input),
                    "result": result,
                    "error": error,
                })
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": json.dumps(result) if error is None else error,
                    "is_error": error is not None,
                })

            messages.append({"role": "user", "content": tool_results})

        return {
            "output": "agent exceeded max turns without finishing",
            "tool_calls": tool_calls_log,
        }

    def real_tool_fn(name: str, args: dict):
        return dispatcher.call(name, args)

    return asyncio.run(stress_test(
        agent_fn=agent_fn,
        real_tool_fn=real_tool_fn,
        tools=dispatcher.names,
        user_inputs=test_inputs,
        agent_name=agent_name,
        scenarios=scenarios,
        parallel=parallel,
        agent_timeout_s=agent_timeout_s,
        on_progress=on_progress,
    ))
