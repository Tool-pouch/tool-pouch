# Changelog

All notable changes to Tool Pouch are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] — 2026-05-17

Initial public release under the `tool-pouch` name. Previously developed
internally under a different name; this release is a clean break with no
import-path or environment-variable back-compat.

### Capabilities

- Pre-deploy stress testing — `tool-pouch scan`, `tool-pouch run`,
  adapters for OpenAI and Anthropic tool calling, MCP support, and
  fix-prompt output.
- Production capture — `tool_pouch.wrap_openai(client)` and
  `tool_pouch.wrap_anthropic(client)` intercept every
  `chat.completions.create` / `messages.create` call with
  sub-millisecond enqueue overhead. Sync, async, and streaming clients
  are supported; tool-call deltas are reassembled before commit.
- Background writer thread — serialization, redaction, truncation, and
  destination IO all run off the request path. Daemon thread, fail-open
  per destination, multi-process safe via `os.register_at_fork`.
- Three destinations: `tool_pouch.LocalStore` (SQLite at
  `~/.tool_pouch/tool_pouch.db`), `tool_pouch.JSONLogger` (NDJSON to a
  stream), `tool_pouch.HTTPSink` (batched POST). All conform to a
  single `Destination` Protocol.
- Per-trace size limits with structured truncation markers.
- `tool_pouch.redact.builtin()` PII redactor — emails, phones, SSNs,
  credit cards, IPv4/IPv6, OpenAI/Anthropic keys, AWS keys, GitHub
  tokens, generic bearer tokens. Extensible via `extra_patterns=`.
  Custom callables are supported as `redact=` directly.
- `tool_pouch.flush(timeout)` public API to drain in-flight traces
  before process exit. Also wired to `atexit`.
- Replay — `tool-pouch replay <id>` re-runs any captured trace under
  `--frozen` / `--frozen-tools` (with `--strict | --loose-tools |
  --match-closest`) / chaos (default). Use `--repeat N` for aggregated
  failure-rate reporting.
- CLI: `tool-pouch traces`, `tool-pouch trace <id>`, `tool-pouch sync`
  (cloud-sync stub).
- Versioned migration system at `tool_pouch/migrations/` with a
  `schema_version` table. Idempotent re-runs.
- SQLite WAL mode + `synchronous=NORMAL` for safe multi-process
  concurrency.
- One-time CLI nudges via `tool_pouch/nudges.py`
  (`~/.tool_pouch/nudges.json` state).
- `TOOL_POUCH_DISABLE_WRAP=1` short-circuits production capture to
  passthrough — useful in CI and unit tests.
