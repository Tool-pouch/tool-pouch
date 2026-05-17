"""Per-trace size limits.

Long conversations and large tool outputs can balloon a single trace to
many MB. Without limits, SQLite blobs and the writer queue both suffer.

Two limits, both enforced on the writer thread (not the sync path):

    max_tool_result_kb     each tool result truncated above this size
    max_trace_kb           overall trace JSON capped, middle messages
                            replaced with a marker

Configurable via .tool_pouch.toml:

    [tool_pouch.wrap]
    max_tool_result_kb = 100
    max_trace_kb = 1024

Truncation is non-destructive in spirit — markers preserve enough
context that the reader knows truncation happened and what was lost.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List


DEFAULT_MAX_TOOL_RESULT_KB = 100
DEFAULT_MAX_TRACE_KB = 1024


def truncate_tool_result(result: Any, max_kb: int = DEFAULT_MAX_TOOL_RESULT_KB) -> Any:
    """Truncate one tool result's payload if it exceeds the cap.

    Returns the original value when small enough; a structured marker
    dict when truncated, including a preview of the original.
    """
    if max_kb <= 0:
        return result
    serialized = _try_json(result)
    if serialized is None:
        return result
    size_bytes = len(serialized.encode("utf-8"))
    if size_bytes <= max_kb * 1024:
        return result
    preview_chars = min(512, max_kb * 1024 // 4)
    return {
        "_truncated": True,
        "original_size_bytes": size_bytes,
        "preview": serialized[:preview_chars],
    }


def truncate_trace(
    trace: Dict[str, Any],
    max_kb: int = DEFAULT_MAX_TRACE_KB,
    max_tool_result_kb: int = DEFAULT_MAX_TOOL_RESULT_KB,
) -> Dict[str, Any]:
    """Truncate an entire trace dict to fit under `max_kb`.

    Strategy:
      1. Truncate per-tool-result payloads first (cheap, surgical).
      2. If the trace is still too large, drop middle messages and
         leave a marker.
    """
    if "tool_calls" in trace and isinstance(trace["tool_calls"], list):
        trace["tool_calls"] = [
            _truncate_tool_call(tc, max_tool_result_kb)
            for tc in trace["tool_calls"]
        ]

    if max_kb <= 0:
        return trace

    serialized = _try_json(trace)
    if serialized is None or len(serialized.encode("utf-8")) <= max_kb * 1024:
        return trace

    messages = trace.get("messages")
    if isinstance(messages, list) and len(messages) > 6:
        keep_head = 1
        keep_tail = 5
        head = messages[:keep_head]
        tail = messages[-keep_tail:]
        dropped = len(messages) - keep_head - keep_tail
        trace["messages"] = head + [
            {
                "_truncated_messages": True,
                "dropped_count": dropped,
                "note": (
                    f"Tool Pouch dropped {dropped} middle messages to keep this "
                    f"trace under {max_kb}KB."
                ),
            }
        ] + tail

    return trace


def _truncate_tool_call(tc: Dict[str, Any], max_kb: int) -> Dict[str, Any]:
    if "result" in tc:
        tc["result"] = truncate_tool_result(tc["result"], max_kb)
    return tc


def _try_json(obj: Any) -> str | None:
    try:
        return json.dumps(obj, default=str)
    except (TypeError, ValueError):
        return None
