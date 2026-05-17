"""Path 3 of 3: Incident replay.

Replay is a CLI workflow. This file is the agent definition that the
replay engine drives when running in `--frozen-tools` or chaos mode.
It uses Path C (custom orchestration) from the README so non-OpenAI
/Anthropic stacks (LangGraph, MCP, your own loop) can be replayed.
Stacks already on Path A (decorator) or Path D (production wrap) get
this for free, no agent file required.

Find a captured incident, then replay it:
    pouch traces --since 24h --failed
    pouch trace abc12345

    # Walk through what actually happened. No API calls.
    pouch replay abc12345 --frozen --agent-file examples/03_incident_replay.py

    # Re-call your model; stub tools with captured outputs.
    pouch replay abc12345 --frozen-tools --agent-file examples/03_incident_replay.py

    # Default: full chaos. Real model, real tools, injected scenarios.
    pouch replay abc12345 --agent-file examples/03_incident_replay.py

    # 100 chaos replays produce a percentage verdict per scenario,
    # answering "would this incident reproduce?" with a number.
    pouch replay abc12345 --repeat 100 --agent-file examples/03_incident_replay.py

The `--agent-file` flag can be omitted by setting `agent` in
`.tool_pouch.toml` at the repo root.
"""


def real_tool_fn(name, args):
    if name == "search":
        return {"results": [{"title": "Real result", "url": "https://example.com"}]}
    if name == "fetch":
        return {"content": "Real page content"}
    raise ValueError(f"Unknown tool: {name}")


async def agent_fn(user_input, tool_caller):
    """Same agent shape as pre-deploy. The replay engine passes the
    captured input back through `agent_fn` and dispatches tool calls
    through Tool Pouch's failure-injection proxy."""
    tool_calls = []

    search_result = await tool_caller("search", {"q": user_input})
    tool_calls.append({"tool": "search", "args": {"q": user_input}, "result": search_result})

    first_url = search_result["results"][0]["url"]
    fetched = await tool_caller("fetch", {"url": first_url})
    tool_calls.append({"tool": "fetch", "args": {"url": first_url}, "result": fetched})

    return {
        "output": f"I found and fetched: {fetched['content']}",
        "tool_calls": tool_calls,
    }


tools = ["search", "fetch"]
