"""Per-trace size truncation."""
from tool_pouch.wrap.limits import (
    DEFAULT_MAX_TOOL_RESULT_KB,
    truncate_tool_result,
    truncate_trace,
)


def test_small_tool_result_is_passthrough():
    assert truncate_tool_result({"a": 1}) == {"a": 1}


def test_large_tool_result_truncated_with_marker():
    big = "x" * (DEFAULT_MAX_TOOL_RESULT_KB * 1024 + 5_000)
    out = truncate_tool_result(big)
    assert out["_truncated"] is True
    assert out["original_size_bytes"] >= DEFAULT_MAX_TOOL_RESULT_KB * 1024
    assert "preview" in out
    assert len(out["preview"]) <= 1024


def test_zero_max_kb_disables_truncation():
    big = "x" * 1_000_000
    assert truncate_tool_result(big, max_kb=0) == big


def test_truncate_trace_drops_middle_messages_when_too_large():
    large_chunk = "y" * 200_000
    messages = [{"role": "user", "content": large_chunk} for _ in range(20)]
    trace = {"user_input": "hi", "messages": messages, "tool_calls": []}

    out = truncate_trace(trace, max_kb=100, max_tool_result_kb=999)
    assert any(m.get("_truncated_messages") for m in out["messages"])
    assert len(out["messages"]) < 20


def test_truncate_trace_truncates_tool_results_first():
    big = "z" * (DEFAULT_MAX_TOOL_RESULT_KB * 1024 + 1024)
    trace = {
        "user_input": "hi",
        "messages": [],
        "tool_calls": [{"name": "search", "result": big}],
    }
    out = truncate_trace(trace, max_kb=2048)
    assert out["tool_calls"][0]["result"]["_truncated"] is True


def test_truncate_trace_passthrough_when_small():
    trace = {"user_input": "hi", "messages": [{"role": "u", "content": "x"}]}
    assert truncate_trace(trace) == trace
