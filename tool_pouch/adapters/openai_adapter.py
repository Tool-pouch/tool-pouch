"""OpenAI chat-completions tool-calling adapter.

Drives the model->tool->model loop the user would normally write themselves,
so they only have to point us at their client and tools.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, List, Optional

from tool_pouch._introspect import to_openai
from tool_pouch.adapters._common import build_dispatcher
from tool_pouch.adapters._judge_default import default_judge_to
from tool_pouch.runner import DEFAULT_AGENT_TIMEOUT_S


DEFAULT_SYSTEM_PROMPT = "You are a helpful assistant. Use the provided tools when relevant."
MAX_AGENT_TURNS = 10  # Safety net so a runaway model can't burn tokens forever


def test_openai(
    *,
    client: Any,
    model: str,
    tools: List[Any],
    test_inputs: List[str],
    system: str = DEFAULT_SYSTEM_PROMPT,
    scenarios: Optional[List[str]] = None,
    parallel: int = 8,
    agent_timeout_s: int = DEFAULT_AGENT_TIMEOUT_S,
    agent_name: str = "openai_agent",
    on_progress: Optional[Any] = None,
) -> List[str]:
    """Stress-test an OpenAI chat-completions agent. Returns a list of run_ids.

    Pass plain Python functions in `tools` — schemas are derived from
    signature + docstring. To override, pass `pouch.tool` objects directly.
    """
    from tool_pouch import stress_test

    default_judge_to("openai")

    dispatcher, specs = build_dispatcher(tools)
    tool_defs = [to_openai(s) for s in specs]

    async def agent_fn(user_input: str, tool_caller):
        messages: List[dict] = [
            {"role": "system", "content": system},
            {"role": "user", "content": user_input},
        ]
        tool_calls_log: List[dict] = []

        for _ in range(MAX_AGENT_TURNS):
            response = await asyncio.to_thread(
                client.chat.completions.create,
                model=model,
                messages=messages,
                tools=tool_defs,
            )
            msg = response.choices[0].message
            messages.append(msg.model_dump(exclude_none=True))

            if not msg.tool_calls:
                return {"output": msg.content or "", "tool_calls": tool_calls_log}

            for tc in msg.tool_calls:
                args = json.loads(tc.function.arguments or "{}")
                try:
                    result = await tool_caller(tc.function.name, args)
                    error = None
                except Exception as e:
                    result = None
                    error = f"{type(e).__name__}: {e}"

                tool_calls_log.append({
                    "tool": tc.function.name,
                    "args": args,
                    "result": result,
                    "error": error,
                })
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": json.dumps(result) if error is None else error,
                })

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
