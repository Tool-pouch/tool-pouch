# Tool Pouch

**Stress-test agents. Capture production. Replay incidents on demand.**

Tool Pouch is the reliability layer for AI agents. It catches silent failures
pre-deploy with `tool-pouch scan`, captures every production request with
`tool_pouch.wrap_openai`, and replays any captured trace under chaos so you
can answer **"would this incident reproduce?"** in one command — before
you ship a fix.

```bash
pip install tool-pouch
```

```python
import tool_pouch
from openai import OpenAI

# Wrap once. Every chat.completions.create from here on is captured.
client = tool_pouch.wrap_openai(OpenAI())
```

```bash
# Pre-deploy
tool-pouch init && tool-pouch scan --quick

# In production, after the wrap()
tool-pouch traces --since 1h --failed       # what's blowing up?
tool-pouch trace --request-id req-abc       # one specific request
tool-pouch replay <trace_id> --repeat 100   # would it reproduce?
```

---

## What Tool Pouch is for

Three problems, one toolkit:

| Layer | Command | What it answers |
|---|---|---|
| **Pre-deploy** | `tool-pouch scan` | "What does my agent do when its tools break?" |
| **Production** | `tool_pouch.wrap_openai` | "What did my agent actually receive and emit?" |
| **Incident response** | `tool-pouch replay` | "Would this 3am incident reproduce?" |

You can adopt any one independently. They share the same data model
(local SQLite by default; pluggable destinations for production), so
captured traces become testable scenarios with no extra plumbing.

---

## Install

```bash
pip install tool-pouch
```

For OpenAI or Ollama support:

```bash
pip install tool-pouch[openai]   # OpenAI or any OpenAI-compatible endpoint
pip install tool-pouch[ollama]   # Local Ollama
```

---

## LLM provider

Tool Pouch uses an LLM to classify failures (hallucinated vs handled,
silent_wrong, etc.) and suggest fixes. **One API key is enough** —
`tool-pouch init` autodetects which one you have, and `tool-pouch scan` mirrors
the agent provider to the judge by default.

```bash
export OPENAI_API_KEY=...        # → provider = openai, judge = openai
# or
export ANTHROPIC_API_KEY=...     # → provider = anthropic, judge = anthropic
```

Override the judge for a single run:

```bash
tool-pouch scan --judge ollama        # local, fully offline
tool-pouch run my_agent.py --judge openai
```

If the judge can't reach the LLM (no network, model down), Tool Pouch still
runs — crashes, timeouts, and loops are detected without it. Only the
nuanced "did this hallucinate?" classification needs the judge.

Supported: **Anthropic**, **OpenAI**, **Ollama** (local, fully offline).

---

## Pick your path

Four integration paths. Each one is five minutes or less. Pick whichever
matches your existing setup.

| Use this if... | Path | Jump to |
|---|---|---|
| Tools are plain `.py` functions you control | **A. Decorator** | [`@tool_pouch.tool` + `tool-pouch scan`](#path-a--decorator-the-simplest) |
| You already use OpenAI / Anthropic tool calling | **B. Adapter** | [`test_openai` / `test_anthropic`](#path-b--openai--anthropic-adapter) |
| LangGraph, MCP, or your own loop | **C. Custom orchestration** | [`agent_fn` + `tool-pouch run`](#path-c--custom-orchestration) |
| You want production capture + replay | **D. wrap()** | [`wrap_openai` / `wrap_anthropic`](#path-d--production-wrap--replay) |

What success looks like in any of them: see [What the output looks like](#what-the-output-looks-like).

---

## Path A — Decorator (the simplest)

> ~5 min. Use this when tools are plain Python functions in your own files.

```bash
tool-pouch init             # autodetects tools/, provider, model
tool-pouch scan --quick     # ~15s; runs the highest-signal scenarios first
```

Tag the functions you want tested:

```python
# tools/web.py
from tool_pouch import tool

@tool
def search(q: str) -> dict:
    """Search the web for q."""
    return search_api(q)

@tool
def fetch(url: str) -> dict:
    """Fetch the URL and return content."""
    return requests.get(url).json()
```

`tool-pouch init` finds your tools folder, picks the right provider based on
your API key, and writes `.tool_pouch.toml`. The judge defaults to the same
provider as the agent — one API key is enough.

`--quick` mode runs one input across the four highest-signal failure
scenarios, designed for fix → re-run → verify cycles. Drop the flag for
the full battery (12 scenarios × N inputs).

---

## Path B — OpenAI / Anthropic adapter

> ~5 min. Use this when you already have a working agent on OpenAI or
> Anthropic tool calling.

Schemas are derived from each function's signature and docstring — no
separate spec file, no rewrite.

```python
import tool_pouch
from openai import OpenAI

def search(q: str) -> dict:
    """Search the web for q."""
    return {"results": [...]}

tool_pouch.test_openai(
    client=OpenAI(),
    model="gpt-4o",
    tools=[search],
    test_inputs=["best pizza in NYC"],
)
```

Anthropic is identical:

```python
from anthropic import Anthropic

tool_pouch.test_anthropic(
    client=Anthropic(),
    model="claude-opus-4-7",
    tools=[search],
    test_inputs=["best pizza in NYC"],
)
```

The adapter drives the model loop, dispatches tool calls through Tool Pouch's
failure-injection proxy, and returns a list of `run_id`s — same coverage
as Path A, none of the boilerplate.

---

## Path C — Custom orchestration

> ~10 min. Use this when you're not on OpenAI / Anthropic directly —
> LangGraph, Pydantic-AI, MCP, or your own loop.

Define four exports in a Python file:

```python
# my_agent.py

async def agent_fn(user_input, tool_caller):
    # Use tool_caller(name, args) to call your tools.
    result = await tool_caller("search", {"q": user_input})
    return {"output": "...", "tool_calls": [...]}

def real_tool_fn(name, args):
    if name == "search":
        return search_api(args["q"])
    ...

tools = ["search", "fetch"]            # tools to inject failures into
test_inputs = ["best pizza in NYC"]    # what to ask your agent
```

Run it:

```bash
tool-pouch run my_agent.py
```

---

## Path D — Production wrap + replay

> ~5 min. Use this when you want every production request captured and
> any of them replayable on demand.

One line wraps your client:

```python
import tool_pouch
from openai import OpenAI

client = tool_pouch.wrap_openai(OpenAI(), agent_name="support_bot")
# That's it. Use client.chat.completions.create exactly as before.
```

Anthropic is identical:

```python
client = tool_pouch.wrap_anthropic(Anthropic(), agent_name="support_bot")
```

Async clients work too (`AsyncOpenAI`, `AsyncAnthropic`). Streaming is
fully supported — chunks pass through unchanged, and the trace is
committed when the stream exhausts.

### Querying captured traces

```bash
tool-pouch traces                            # everything captured
tool-pouch traces --since 1h --failed        # last hour, failures only
tool-pouch traces --request-id req-abc       # by your request_id
tool-pouch trace <trace_id>                  # full detail of one capture
```

`request_id` flows through to traces — pass a string or a callable that
extracts it from the request kwargs:

```python
client = tool_pouch.wrap_openai(
    OpenAI(),
    request_id=lambda **kw: kw.get("user", "anon"),
)
```

### Replaying

```bash
# Walk through what actually happened — no API calls.
tool-pouch replay <trace_id> --frozen

# Re-call your model; stub tools with captured outputs.
tool-pouch replay <trace_id> --frozen-tools

# Default: chaos. Real model, real tools, injected scenarios.
tool-pouch replay <trace_id>

# 100 chaos replays → "would this incident reproduce?"
tool-pouch replay <trace_id> --repeat 100
```

For chaos / frozen-tools modes, Tool Pouch needs your `agent_fn` and (for
chaos) your `real_tool_fn`. Set `agent` in `.tool_pouch.toml` or pass
`--agent-file my_agent.py` (same shape as Path C).

`--repeat N` aggregates verdicts as percentages per (tool, scenario)
cell — useful for surfacing flaky failure rates.

### PII redaction

The default redactor scrubs emails, phones, SSNs, credit cards, IPs,
and common API keys at capture time:

```python
client = tool_pouch.wrap_openai(OpenAI())   # built-in redaction enabled
```

Extend the regex pack:

```python
client = tool_pouch.wrap_openai(
    OpenAI(),
    redact=tool_pouch.redact.builtin(extra_patterns=[
        r"acct_\d{6}",
        r"customer_token=[A-Za-z0-9]+",
    ]),
)
```

Disable redaction explicitly (if you're handling PII upstream):

```python
client = tool_pouch.wrap_openai(OpenAI(), redact=None)
```

### Destinations

Three destinations ship in OSS. Combine them — capture once, pipe
anywhere:

```python
client = tool_pouch.wrap_openai(
    OpenAI(),
    destinations=[
        tool_pouch.LocalStore(),                       # SQLite, dev/staging
        tool_pouch.JSONLogger(),                       # NDJSON to stderr
        tool_pouch.HTTPSink(url="https://your.api/traces"),
    ],
)
```

| Destination | Use it for |
|---|---|
| `LocalStore` | Dev / staging. SQLite at `~/.tool_pouch/tool_pouch.db`. |
| `JSONLogger` | Production. Pipe stderr into Datadog, Honeycomb, Loki, CloudWatch. |
| `HTTPSink` | In-house observability backends. Batched POST. |

A future `CloudStore` will become a fourth destination after Tool Pouch Cloud
ships. The wrap API stays unchanged.

### Disabling capture

Set `TOOL_POUCH_DISABLE_WRAP=1` and every `wrap_openai` / `wrap_anthropic`
call becomes a no-op passthrough. Useful in CI and unit tests.

### What the wrap costs you

Sub-millisecond p99 enqueue overhead on the request thread.
Serialization, redaction, truncation, and destination IO all run on a
background writer thread. Multi-process safe (pre-fork models like
gunicorn / uvicorn workers). Per-trace size limits prevent runaway
payloads. Fail-open at every destination — a misbehaving sink logs to
stderr and never propagates.

---

## What the output looks like

```
============================================================
Agent Test Report (run abc12345)
============================================================
Total scenarios: 24
Failures: 14 (58%)

Breakdown:
  ❌ crashed: 8
  ❌ hallucinated: 4
  ❌ looped: 2
  ✓ handled: 10

For full trace of any failure: tool-pouch show abc12345 --filter <type>
```

Exit code is `0` when all scenarios pass and `1` when any fail — works
in CI out of the box.

---

## Drilling in & re-running

```bash
tool-pouch show abc12345 --filter hallucinated      # full trace of one type
tool-pouch scan --scenarios timeout,malformed_json  # re-run a slice
tool-pouch run my_agent.py --tools search           # one tool only
tool-pouch runs --failed                            # history, failures only
```

---

## Project config (`.tool_pouch.toml`)

```toml
[tool-pouch]
# For `tool-pouch scan`
tools = "./my_app/tools/"
provider = "openai"
model = "gpt-4o"
test_inputs = ["best pizza in NYC"]   # optional — autogenerated otherwise

# For `tool-pouch run` and `tool-pouch replay`
agent = "./my_agent.py"

# Common
parallel = 8
# scenarios = ["timeout", "malformed_json"]    # optional filter
```

---

## Fix bugs in your AI editor

After any run, get a markdown prompt designed for Cursor, Claude Code,
Cline, Windsurf, or Aider:

```bash
tool-pouch fix-prompt | pbcopy             # latest run → clipboard
```

The format groups failures by source (control flow, prompt, integration)
so your AI editor proposes clustered fixes instead of one-line patches.

---

## Architecture

User-facing surface:

- `tool.py` — `@tool_pouch.tool` decorator + module-level registry
- `discover.py` — walks a path, returns every `@tool_pouch.tool` callable
- `init.py` — `tool-pouch init`, autodetects tools/provider/model
- `autogen.py` — generates test prompts from tool docstrings
- `adapters/` — drop-in helpers for OpenAI and Anthropic tool calling
- `_introspect.py` — Python callables → JSON tool schemas
- `fix_prompt.py` — renders a past run as markdown for AI coding tools

Wrap / replay:

- `wrap/proxy.py` — `wrap_openai` / `wrap_anthropic` client interception
- `wrap/writer.py` — background writer thread, fail-open, fork-safe
- `wrap/destinations.py` — `LocalStore`, `JSONLogger`, `HTTPSink`
- `wrap/limits.py` — per-trace + per-tool-result size truncation
- `redact.py` — PII redaction pack (extensible)
- `replay.py` — `build_replay_inputs(trace, mode=...)` + verdict aggregation
- `nudges.py` — one-time CLI nudges (cloud upgrade hooks)

Engine:

- `proxy.py` — wraps tool calls during stress testing
- `runner.py` — runs (tool × scenario) in parallel; judge fan-out
- `scenarios/static.py` — built-in failures
- `judges/llm_judge.py` — classifies completed runs
- `config.py` — judge provider resolution
- `store.py` — versioned SQLite (WAL mode, multi-process safe)
- `migrations/` — versioned schema migrations
- `report.py` — summary + detailed trace view

---

## Status & roadmap

0.1 ships pre-deploy stress testing, production capture, and replay.
Tool Pouch Cloud is the next layer: push captured traces from any
environment, search by request_id, replay across your team, retain
for compliance. Until it ships, the OSS path is already
production-ready via `JSONLogger` and `HTTPSink`.

Get notified at launch: [toolpouch.dev](https://toolpouch.dev).

---

## License

Apache License 2.0. See [LICENSE](./LICENSE) for the full text and
[NOTICE](./NOTICE) for required attribution.
