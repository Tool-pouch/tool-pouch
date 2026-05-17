"""Generate plausible user prompts from a set of tool descriptions.

Reuses the same provider plumbing as the LLM judge so users don't
configure twice. If the LLM is unreachable, falls back to a generic
prompt so `pouch scan` still produces a result.
"""
from __future__ import annotations

import json
import os
from typing import Any, List

from tool_pouch._introspect import ToolSpec, normalize


_PROMPT = """You are generating prompts to test an AI agent.

The agent has access to these tools:

{tools}

Generate exactly {n} short, realistic user requests this agent might
receive in production. Each request should plausibly require one or more
of the tools above. Keep each request to one sentence.

Return ONLY a JSON array of strings, no preamble:
["request 1", "request 2", ...]"""


def autogen_inputs(tools: List[Any], n: int = 3) -> List[str]:
    """Return n synthetic test inputs derived from the tool set."""
    specs = normalize(tools)
    tool_lines = "\n".join(
        f"- {s.name}: {s.description}" for s in specs
    )
    prompt = _PROMPT.format(tools=tool_lines, n=n)

    try:
        text = _call_llm(prompt)
        result = _parse_array(text)
        if not result:
            return _fallback(n)
        return result[:n]
    except Exception:
        return _fallback(n)


def _fallback(n: int) -> List[str]:
    """Generic prompts so scan never blocks on a missing LLM."""
    base = [
        "Help me with a typical task an agent should handle.",
        "Look something up and summarize what you find.",
        "Fetch the most relevant result and tell me about it.",
    ]
    return base[:n] if n <= len(base) else base * ((n // len(base)) + 1)


def _parse_array(text: str) -> List[str]:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```")[1].lstrip("json\n")
    data = json.loads(text)
    if not isinstance(data, list):
        return []
    return [str(x) for x in data if isinstance(x, str)]


def _call_llm(prompt: str) -> str:
    """Use the same provider plumbing as the judge."""
    provider = os.environ.get("AGENT_SIM_JUDGE_PROVIDER", "anthropic").lower()

    if provider == "anthropic":
        import anthropic
        model = os.environ.get("AGENT_SIM_JUDGE_MODEL", "claude-opus-4-7")
        client = anthropic.Anthropic()
        msg = client.messages.create(
            model=model,
            max_tokens=500,
            messages=[{"role": "user", "content": prompt}],
        )
        return msg.content[0].text

    if provider in ("openai", "ollama"):
        from openai import OpenAI
        if provider == "ollama":
            base_url = os.environ.get("AGENT_SIM_JUDGE_BASE_URL",
                                      "http://localhost:11434/v1")
            api_key = "ollama"
            default_model = "llama3.1"
        else:
            base_url = os.environ.get("AGENT_SIM_JUDGE_BASE_URL")
            api_key = os.environ.get("OPENAI_API_KEY")
            default_model = "gpt-4o-mini"

        model = os.environ.get("AGENT_SIM_JUDGE_MODEL", default_model)
        client = OpenAI(api_key=api_key, base_url=base_url)
        msg = client.chat.completions.create(
            model=model,
            max_tokens=500,
            messages=[{"role": "user", "content": prompt}],
        )
        return msg.choices[0].message.content or ""

    raise RuntimeError(f"Unknown provider: {provider}")
