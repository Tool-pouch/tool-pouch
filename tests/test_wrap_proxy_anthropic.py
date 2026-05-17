"""wrap_anthropic: sync, async, streaming. Uses fake Anthropic shape."""
import io
import json
from types import SimpleNamespace
from typing import List

import pytest

import tool_pouch
from tool_pouch.wrap.destinations import JSONLogger
from tool_pouch.wrap.writer import set_destinations


# --- fakes ------------------------------------------------------------------


def _make_response(text: str = "hi", tool_name: str = None, tool_input: dict = None):
    blocks = [SimpleNamespace(type="text", text=text)]
    if tool_name:
        blocks.append(SimpleNamespace(
            type="tool_use", id="tu_1", name=tool_name, input=tool_input or {},
        ))
    return SimpleNamespace(content=blocks)


class _FakeSyncAnthropic:
    def __init__(self, response=None, raises=None):
        self._response = response or _make_response()
        self._raises = raises
        self.last_kwargs = None
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.last_kwargs = kwargs
        if self._raises:
            raise self._raises
        return self._response


class _FakeAsyncAnthropic:
    def __init__(self, response=None):
        self._response = response or _make_response()
        self.messages = SimpleNamespace(create=self._create)

    async def _create(self, **kwargs):
        return self._response


@pytest.fixture
def buffer_destination():
    tool_pouch.flush(timeout=2.0)
    buf = io.StringIO()
    set_destinations([JSONLogger(stream=buf)])
    yield buf
    tool_pouch.flush(timeout=2.0)
    set_destinations([])


def _wait_for_lines(buf: io.StringIO, count: int, timeout: float = 2.0) -> List[dict]:
    tool_pouch.flush(timeout=timeout)
    lines = [line for line in buf.getvalue().strip().split("\n") if line]
    assert len(lines) >= count
    return [json.loads(line) for line in lines]


# --- tests ------------------------------------------------------------------


def test_sync_passthrough_returns_original_response(buffer_destination):
    expected = _make_response(text="hi back")
    fake = _FakeSyncAnthropic(response=expected)
    client = tool_pouch.wrap_anthropic(fake, agent_name="ant")

    response = client.messages.create(
        model="claude-3", system="be terse",
        messages=[{"role": "user", "content": "hi"}],
    )
    assert response is expected


def test_sync_capture_records_user_input_and_text(buffer_destination):
    fake = _FakeSyncAnthropic(response=_make_response(text="hello back"))
    client = tool_pouch.wrap_anthropic(fake, agent_name="ant_capture")

    client.messages.create(
        model="claude-3",
        system="be helpful",
        messages=[{"role": "user", "content": "hi"}],
    )
    [record] = _wait_for_lines(buffer_destination, 1)
    assert record["agent_name"] == "ant_capture"
    assert record["trace"]["user_input"] == "hi"
    assert record["trace"]["model"] == "claude-3"
    assert record["trace"]["system"] == "be helpful"
    assert any(
        m.get("role") == "assistant" and "hello back" in (m.get("content") or "")
        for m in record["trace"]["messages"]
    )


def test_sync_capture_records_tool_use(buffer_destination):
    fake = _FakeSyncAnthropic(
        response=_make_response(text="ok", tool_name="search", tool_input={"q": "x"})
    )
    client = tool_pouch.wrap_anthropic(fake, agent_name="ant_tools")

    client.messages.create(
        model="claude-3",
        messages=[{"role": "user", "content": "look it up"}],
        tools=[{"name": "search", "description": "..."}],
    )
    [record] = _wait_for_lines(buffer_destination, 1)
    [tc] = record["trace"]["tool_calls"]
    assert tc["name"] == "search"
    assert tc["arguments"] == {"q": "x"}


def test_sync_capture_failure_re_raises_and_records_crashed(buffer_destination):
    fake = _FakeSyncAnthropic(raises=RuntimeError("api down"))
    client = tool_pouch.wrap_anthropic(fake, agent_name="ant_fail")

    with pytest.raises(RuntimeError, match="api down"):
        client.messages.create(
            model="claude-3", messages=[{"role": "user", "content": "x"}]
        )
    [record] = _wait_for_lines(buffer_destination, 1)
    assert record["outcome"] == "crashed"
    assert "api down" in record["trace"]["error"]


async def test_async_passthrough_and_capture(buffer_destination):
    expected = _make_response(text="async ok")
    fake = _FakeAsyncAnthropic(response=expected)
    client = tool_pouch.wrap_anthropic(fake, agent_name="ant_async")

    response = await client.messages.create(
        model="claude-3", messages=[{"role": "user", "content": "go"}]
    )
    assert response is expected
    [record] = _wait_for_lines(buffer_destination, 1)
    assert record["agent_name"] == "ant_async"


# --- streaming --------------------------------------------------------------


def _msg_start(model="claude-3"):
    return SimpleNamespace(type="message_start",
                           message=SimpleNamespace(model=model))


def _content_block_start(index, type_, id_=None, name=None):
    block = SimpleNamespace(type=type_, id=id_, name=name)
    return SimpleNamespace(type="content_block_start", index=index,
                            content_block=block)


def _text_delta(index, text):
    delta = SimpleNamespace(type="text_delta", text=text)
    return SimpleNamespace(type="content_block_delta", index=index, delta=delta)


def _input_json_delta(index, partial):
    delta = SimpleNamespace(type="input_json_delta", partial_json=partial)
    return SimpleNamespace(type="content_block_delta", index=index, delta=delta)


def test_sync_streaming_text_capture(buffer_destination):
    events = [
        _msg_start(),
        _content_block_start(0, "text"),
        _text_delta(0, "Hel"),
        _text_delta(0, "lo"),
    ]

    class _FakeStream(_FakeSyncAnthropic):
        def __init__(self):
            super().__init__(response=iter(events))

    client = tool_pouch.wrap_anthropic(_FakeStream(), agent_name="ant_stream")
    stream = client.messages.create(
        model="claude-3",
        messages=[{"role": "user", "content": "hi"}],
        stream=True,
    )
    received = list(stream)
    assert len(received) == 4

    [record] = _wait_for_lines(buffer_destination, 1)
    assert record["trace"]["stream"] is True
    assistant = record["trace"]["messages"][-1]
    assert assistant["content"] == "Hello"


def test_sync_streaming_tool_use_deltas_reassembled(buffer_destination):
    events = [
        _msg_start(),
        _content_block_start(0, "tool_use", id_="tu_1", name="search"),
        _input_json_delta(0, '{"q":'),
        _input_json_delta(0, ' "weather"}'),
    ]

    class _FakeStream(_FakeSyncAnthropic):
        def __init__(self):
            super().__init__(response=iter(events))

    client = tool_pouch.wrap_anthropic(_FakeStream(), agent_name="ant_stream_tool")
    list(client.messages.create(
        model="claude-3",
        messages=[{"role": "user", "content": "look up"}],
        stream=True,
    ))

    [record] = _wait_for_lines(buffer_destination, 1)
    [tc] = record["trace"]["tool_calls"]
    assert tc["name"] == "search"
    assert tc["arguments"] == '{"q": "weather"}'
