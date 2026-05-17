"""Top-level entry point. Most users will only import from here."""
from tool_pouch.runner import DEFAULT_AGENT_TIMEOUT_S, Runner
from tool_pouch.report import summary, show
from tool_pouch.adapters import test_anthropic, test_openai
from tool_pouch.tool import tool, registered as registered_tools
from tool_pouch.discover import discover
from tool_pouch.wrap import (
    HTTPSink,
    JSONLogger,
    LocalStore,
    flush,
    wrap_anthropic,
    wrap_openai,
)
from tool_pouch import redact


async def stress_test(agent_fn, real_tool_fn, tools, user_inputs,
                      agent_name="agent", scenarios=None, parallel=8,
                      agent_timeout_s=DEFAULT_AGENT_TIMEOUT_S,
                      on_progress=None):
    """The simplest way to use this. Returns list of run_ids."""
    runner = Runner(agent_fn, real_tool_fn, tools, agent_name,
                    agent_timeout_s=agent_timeout_s)
    if isinstance(user_inputs, str):
        user_inputs = [user_inputs]
    return [await runner.run(i, scenarios=scenarios, parallel=parallel,
                             on_progress=on_progress)
            for i in user_inputs]


__all__ = [
    "Runner",
    "stress_test",
    "summary",
    "show",
    "test_openai",
    "test_anthropic",
    "tool",
    "discover",
    "registered_tools",
    "flush",
    "HTTPSink",
    "JSONLogger",
    "LocalStore",
    "redact",
    "wrap_anthropic",
    "wrap_openai",
]
