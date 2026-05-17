"""Production trace capture for OpenAI / Anthropic agents.

Public surface:

    tool_pouch.wrap_openai(client, ...)
    tool_pouch.wrap_anthropic(client, ...)
    tool_pouch.flush(timeout=2.0)
    tool_pouch.JSONLogger(...)
    tool_pouch.HTTPSink(...)
    tool_pouch.LocalStore(...)

Architecture:

    wrap_openai / wrap_anthropic
              │
              ▼
        proxy (sync/async, streaming)
              │
              ▼
        writer (queue + background thread)
              │
              ▼
        destination (LocalStore | JSONLogger | HTTPSink | CloudStore)

The proxy is in front of the user's request, must be sub-millisecond.
The writer thread does all serialization, redaction, truncation, and
destination IO. Destinations only need to implement `write` and `close`.
"""
from tool_pouch.wrap.destinations import (
    Destination,
    HTTPSink,
    JSONLogger,
    LocalStore,
)
from tool_pouch.wrap.proxy import wrap_anthropic, wrap_openai
from tool_pouch.wrap.writer import flush

__all__ = [
    "Destination",
    "HTTPSink",
    "JSONLogger",
    "LocalStore",
    "flush",
    "wrap_anthropic",
    "wrap_openai",
]
