# Tool Pouch examples

Three runnable examples, one per marketed path. They mirror the [site](https://toolpouch.dev) and the [README](../README.md) so the mental model is the same wherever you encounter Tool Pouch.

| # | Path | Lives at | What it shows |
|---|---|---|---|
| 1 | Pre-deploy | [`01_pre_deploy.py`](./01_pre_deploy.py) | `@tool_pouch.tool`-decorated functions that `tool-pouch scan` auto-discovers. No glue code, no spec file. |
| 2 | Production | [`02_production_capture.py`](./02_production_capture.py) | One-line `wrap_openai` with redaction and dual destinations (SQLite + NDJSON to stderr for log-agent pickup). |
| 3 | Incident response | [`03_incident_replay.py`](./03_incident_replay.py) | The agent definition `tool-pouch replay` drives for frozen-tools and chaos modes. |

## Quickstart

```bash
pip install tool-pouch

# 1. Pre-deploy: scan @tool_pouch.tool functions
tool-pouch scan examples/01_pre_deploy.py --quick

# 2. Production: capture every request from your real app
python examples/02_production_capture.py

# 3. Incident response: replay a captured trace under chaos
tool-pouch traces --since 24h --failed
tool-pouch replay <trace_id> --repeat 100 --agent-file examples/03_incident_replay.py
```

For the four detailed integration paths (decorator, OpenAI/Anthropic adapter, custom orchestration, production wrap), see the [README](../README.md#pick-your-path).
