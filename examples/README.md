# Tool Pouch examples

Three runnable examples, one per marketed path. They mirror the [site](https://toolpouch.dev) and the [README](../README.md) so the mental model is the same wherever you encounter Tool Pouch.

> Installed as `pip install tool-pouch`, imported as `import tool_pouch as pouch`, run as `pouch`. The long form `tool-pouch` works too if you prefer.

| # | Path | Lives at | What it shows |
|---|---|---|---|
| 1 | Pre-deploy | [`01_pre_deploy.py`](./01_pre_deploy.py) | `@pouch.tool`-decorated functions that `pouch scan` auto-discovers. No glue code, no spec file. |
| 2 | Production | [`02_production_capture.py`](./02_production_capture.py) | One-line `pouch.wrap_anthropic` with redaction and dual destinations (SQLite + NDJSON to stderr for log-agent pickup). |
| 3 | Incident response | [`03_incident_replay.py`](./03_incident_replay.py) | The agent definition `pouch replay` drives for frozen-tools and chaos modes. |

## Quickstart

```bash
pip install tool-pouch

# 1. Pre-deploy: scan @pouch.tool functions
pouch scan examples/01_pre_deploy.py --quick

# 2. Production: capture every request from your real app
python examples/02_production_capture.py

# 3. Incident response: replay a captured trace under chaos
pouch traces --since 24h --failed
pouch replay <trace_id> --repeat 100 --agent-file examples/03_incident_replay.py
```

For the four detailed integration paths (decorator, Anthropic/OpenAI adapter, custom orchestration, production wrap), see the [README](../README.md#pick-your-path).
