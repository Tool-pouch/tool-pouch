"""wrap_openai: sync, async, streaming. Uses fake OpenAI shape."""
import io
import json
from types import SimpleNamespace
from typing import List

import pytest

import tool_pouch
from tool_pouch.wrap.destinations import JSONLogger
from tool_pouch.wrap.writer import _writer, set_destinations


# --- fakes ------------------------------------------------------------------


def _make_response(content: str = "hi", tool_name: str = None, tool_args: str = None):
    msg = SimpleNamespace(content=content, tool_calls=None)
    if tool_name:
        msg.tool_calls = [
            SimpleNamespace(
                id="call-1",
                function=SimpleNamespace(name=tool_name, arguments=tool_args or "{}"),
            )
        ]
    return SimpleNamespace(choices=[SimpleNamespace(message=msg)])


class _FakeSyncOpenAI:
    def __init__(self, response=None, raises=None):
        self._response = response or _make_response()
        self._raises = raises
        self.last_kwargs = None
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(create=self._create)
        )

    def _create(self, **kwargs):
        self.last_kwargs = kwargs
        if self._raises:
            raise self._raises
        return self._response


class _FakeAsyncOpenAI:
    def __init__(self, response=None):
        self._response = response or _make_response()
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(create=self._create)
        )

    async def _create(self, **kwargs):
        return self._response


# --- fixtures ---------------------------------------------------------------


@pytest.fixture
def buffer_destination():
    """Replace global writer destinations with an in-memory NDJSON buffer.

    Flushes before AND after so leftover records from prior tests don't
    bleed into this test's buffer (and vice versa).
    """
    tool_pouch.flush(timeout=2.0)
    buf = io.StringIO()
    set_destinations([JSONLogger(stream=buf)])
    yield buf
    tool_pouch.flush(timeout=2.0)
    set_destinations([])


def _wait_for_lines(buf: io.StringIO, count: int, timeout: float = 2.0) -> List[dict]:
    tool_pouch.flush(timeout=timeout)
    lines = [line for line in buf.getvalue().strip().split("\n") if line]
    assert len(lines) >= count, (
        f"expected at least {count} lines, got {len(lines)}: {buf.getvalue()!r}"
    )
    return [json.loads(line) for line in lines]


# --- sync passthrough + capture ---------------------------------------------


def test_sync_passthrough_returns_original_response(buffer_destination):
    expected = _make_response(content="answer")
    fake = _FakeSyncOpenAI(response=expected)
    client = tool_pouch.wrap_openai(fake, agent_name="test_agent")

    response = client.chat.completions.create(
        model="gpt-4", messages=[{"role": "user", "content": "hi"}]
    )
    assert response is expected


def test_sync_capture_records_user_input_and_response(buffer_destination):
    fake = _FakeSyncOpenAI()
    client = tool_pouch.wrap_openai(fake, agent_name="alpha")

    client.chat.completions.create(
        model="gpt-4",
        messages=[
            {"role": "system", "content": "be helpful"},
            {"role": "user", "content": "what is 2+2?"},
        ],
    )

    [record] = _wait_for_lines(buffer_destination, 1)
    assert record["agent_name"] == "alpha"
    assert record["kind"] == "production"
    assert record["outcome"] == "completed"
    assert record["trace"]["user_input"] == "what is 2+2?"
    assert record["trace"]["model"] == "gpt-4"


def test_sync_capture_records_tool_calls(buffer_destination):
    response = _make_response(tool_name="search", tool_args='{"q": "weather"}')
    fake = _FakeSyncOpenAI(response=response)
    client = tool_pouch.wrap_openai(fake, agent_name="alpha")

    client.chat.completions.create(
        model="gpt-4",
        messages=[{"role": "user", "content": "look it up"}],
        tools=[{"type": "function", "function": {"name": "search"}}],
    )
    [record] = _wait_for_lines(buffer_destination, 1)
    assert record["trace"]["tool_calls"][0]["name"] == "search"
    assert "weather" in record["trace"]["tool_calls"][0]["arguments"]


def test_sync_capture_failure_re_raises_and_records_crashed(buffer_destination):
    fake = _FakeSyncOpenAI(raises=RuntimeError("api down"))
    client = tool_pouch.wrap_openai(fake, agent_name="alpha")

    with pytest.raises(RuntimeError, match="api down"):
        client.chat.completions.create(
            model="gpt-4", messages=[{"role": "user", "content": "x"}]
        )
    [record] = _wait_for_lines(buffer_destination, 1)
    assert record["outcome"] == "crashed"
    assert "api down" in record["trace"]["error"]


# --- async ------------------------------------------------------------------


async def test_async_passthrough_returns_original(buffer_destination):
    expected = _make_response(content="async answer")
    fake = _FakeAsyncOpenAI(response=expected)
    client = tool_pouch.wrap_openai(fake, agent_name="async_a")

    response = await client.chat.completions.create(
        model="gpt-4", messages=[{"role": "user", "content": "hi"}]
    )
    assert response is expected


async def test_async_capture(buffer_destination):
    fake = _FakeAsyncOpenAI()
    client = tool_pouch.wrap_openai(fake, agent_name="async_b")

    await client.chat.completions.create(
        model="gpt-4", messages=[{"role": "user", "content": "ping"}]
    )
    [record] = _wait_for_lines(buffer_destination, 1)
    assert record["agent_name"] == "async_b"
    assert record["trace"]["user_input"] == "ping"


# --- streaming --------------------------------------------------------------


def _stream_chunk(content: str = None, tool_index: int = None,
                  tool_id: str = None, tool_name: str = None,
                  tool_args: str = None, model: str = "gpt-4"):
    tool_calls = None
    if tool_index is not None:
        tool_calls = [
            SimpleNamespace(
                index=tool_index,
                id=tool_id,
                function=SimpleNamespace(name=tool_name, arguments=tool_args or ""),
            )
        ]
    delta = SimpleNamespace(content=content, tool_calls=tool_calls)
    return SimpleNamespace(model=model, choices=[SimpleNamespace(delta=delta)])


def test_sync_streaming_passthrough_and_capture(buffer_destination):
    chunks = [
        _stream_chunk(content="Hel"),
        _stream_chunk(content="lo "),
        _stream_chunk(content="world"),
    ]

    class _FakeStream(_FakeSyncOpenAI):
        def __init__(self):
            super().__init__(response=iter(chunks))

    client = tool_pouch.wrap_openai(_FakeStream(), agent_name="stream")
    stream = client.chat.completions.create(
        model="gpt-4",
        messages=[{"role": "user", "content": "hi"}],
        stream=True,
    )

    seen = list(stream)
    assert len(seen) == 3

    [record] = _wait_for_lines(buffer_destination, 1)
    assert record["trace"]["stream"] is True
    assistant = record["trace"]["messages"][-1]
    assert assistant["content"] == "Hello world"


def test_streaming_tool_call_deltas_reassembled(buffer_destination):
    chunks = [
        _stream_chunk(tool_index=0, tool_id="call-1", tool_name="search",
                      tool_args='{"q":'),
        _stream_chunk(tool_index=0, tool_args=' "weather"}'),
    ]

    class _FakeStream(_FakeSyncOpenAI):
        def __init__(self):
            super().__init__(response=iter(chunks))

    client = tool_pouch.wrap_openai(_FakeStream(), agent_name="stream_tools")
    list(
        client.chat.completions.create(
            model="gpt-4",
            messages=[{"role": "user", "content": "weather?"}],
            stream=True,
        )
    )

    [record] = _wait_for_lines(buffer_destination, 1)
    [tc] = record["trace"]["tool_calls"]
    assert tc["name"] == "search"
    assert tc["arguments"] == '{"q": "weather"}'


def test_streaming_partial_failure_records_crashed(buffer_destination):
    def _raising_iter():
        yield _stream_chunk(content="hi ")
        raise RuntimeError("connection lost")

    class _FakeStream(_FakeSyncOpenAI):
        def __init__(self):
            super().__init__(response=_raising_iter())

    client = tool_pouch.wrap_openai(_FakeStream(), agent_name="stream_fail")
    stream = client.chat.completions.create(
        model="gpt-4",
        messages=[{"role": "user", "content": "x"}],
        stream=True,
    )
    with pytest.raises(RuntimeError, match="connection lost"):
        for _ in stream:
            pass
    [record] = _wait_for_lines(buffer_destination, 1)
    assert record["outcome"] == "crashed"
    assert record["trace"]["error"]


# --- redaction integration --------------------------------------------------


def test_default_redactor_scrubs_pii_in_user_input(buffer_destination):
    fake = _FakeSyncOpenAI()
    client = tool_pouch.wrap_openai(fake, agent_name="redact_default")

    client.chat.completions.create(
        model="gpt-4",
        messages=[{"role": "user", "content": "email me at jane@example.com"}],
    )
    [record] = _wait_for_lines(buffer_destination, 1)
    assert "jane@example.com" not in record["trace"]["user_input"]
    assert "[REDACTED]" in record["trace"]["user_input"]


def test_redact_none_disables_scrubbing(buffer_destination):
    fake = _FakeSyncOpenAI()
    client = tool_pouch.wrap_openai(fake, agent_name="no_redact", redact=None)

    client.chat.completions.create(
        model="gpt-4",
        messages=[{"role": "user", "content": "ssn 123-45-6789"}],
    )
    [record] = _wait_for_lines(buffer_destination, 1)
    assert "123-45-6789" in record["trace"]["user_input"]


def test_invalid_redact_at_raises():
    fake = _FakeSyncOpenAI()
    with pytest.raises(ValueError):
        tool_pouch.wrap_openai(fake, redact_at="never")


# --- request_id -------------------------------------------------------------


def test_request_id_string_flows_through(buffer_destination):
    fake = _FakeSyncOpenAI()
    client = tool_pouch.wrap_openai(fake, request_id="req-static")
    client.chat.completions.create(
        model="gpt-4", messages=[{"role": "user", "content": "x"}]
    )
    [record] = _wait_for_lines(buffer_destination, 1)
    assert record["request_id"] == "req-static"


def test_request_id_callable_extracts_from_kwargs(buffer_destination):
    fake = _FakeSyncOpenAI()
    client = tool_pouch.wrap_openai(
        fake, request_id=lambda **kw: kw.get("user", "anon")
    )
    client.chat.completions.create(
        model="gpt-4",
        messages=[{"role": "user", "content": "x"}],
        user="alice",
    )
    [record] = _wait_for_lines(buffer_destination, 1)
    assert record["request_id"] == "alice"


def test_request_id_default_is_unique_uuid(buffer_destination):
    fake = _FakeSyncOpenAI()
    client = tool_pouch.wrap_openai(fake)
    for _ in range(2):
        client.chat.completions.create(
            model="gpt-4", messages=[{"role": "user", "content": "x"}]
        )
    records = _wait_for_lines(buffer_destination, 2)
    ids = {r["request_id"] for r in records}
    assert len(ids) == 2


# --- env disable ------------------------------------------------------------


def test_disable_env_short_circuits_wrap(monkeypatch):
    monkeypatch.setenv("TOOL_POUCH_DISABLE_WRAP", "1")
    fake = _FakeSyncOpenAI()
    original_create = fake.chat.completions.create
    client = tool_pouch.wrap_openai(fake)
    assert client.chat.completions.create is original_create
