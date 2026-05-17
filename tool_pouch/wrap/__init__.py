"""Production trace capture for Anthropic / OpenAI agents.

Public surface (assuming `import tool_pouch as pouch`):

    pouch.wrap_anthropic(client, ...)
    pouch.wrap_openai(client, ...)
    pouch.flush(timeout=2.0)
    pouch.JSONLogger(...)
    pouch.HTTPSink(...)
    pouch.LocalStore(...)

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
