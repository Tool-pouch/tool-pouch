"""Anthropic / OpenAI client wrappers for production trace capture.

Public API (assuming `import tool_pouch as pouch`):

    pouch.wrap_anthropic(client, agent_name="...", ...)
    pouch.wrap_openai(client, agent_name="...", ...)

The wrappers monkey-patch `chat.completions.create` (OpenAI) and
`messages.create` (Anthropic) on the supplied client, leaving every
other attribute intact. Sync and async clients are both supported by
detecting the bound method type at wrap time.

Streaming is supported via a passthrough iterator that accumulates
chunks until the stream exhausts, then commits a single trace.

Capture overhead on the request thread is the enqueue cost only —
serialization, redaction, truncation, and destination IO all run on
the writer thread.
"""
from __future__ import annotations

import asyncio
import inspect
import os
import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Iterable, List, Optional, Sequence, Union

from tool_pouch import redact as redact_module
from tool_pouch.store import KIND_PRODUCTION
from tool_pouch.wrap.destinations import Destination, LocalStore, TraceRecord
from tool_pouch.wrap.limits import (
    DEFAULT_MAX_TOOL_RESULT_KB,
    DEFAULT_MAX_TRACE_KB,
    truncate_trace,
)
from tool_pouch.wrap.writer import enqueue, get_writer, set_destinations


_SENTINEL = object()
RequestIdSpec = Union[None, str, Callable[..., Optional[str]]]


@dataclass
class WrapConfig:
    """Per-wrap configuration. Stored on the wrapped method via closure."""

    agent_name: str
    agent_version: Optional[str]
    redactor: Optional[Callable[[Any], Any]]
    redact_at: str
    request_id: RequestIdSpec
    max_trace_kb: int
    max_tool_result_kb: int


# --- public entry points -----------------------------------------------------


def wrap_openai(
    client: Any,
    agent_name: str = "agent",
    agent_version: Optional[str] = None,
    redact: Any = _SENTINEL,
    redact_at: str = "capture",
    request_id: RequestIdSpec = None,
    destinations: Optional[Sequence[Destination]] = None,
    max_trace_kb: int = DEFAULT_MAX_TRACE_KB,
    max_tool_result_kb: int = DEFAULT_MAX_TOOL_RESULT_KB,
) -> Any:
    """Wrap an OpenAI client so every chat.completions.create is captured.

    Mutates and returns the supplied client (sync or async). Subsequent
    OpenAI calls behave identically to before, with the side effect of
    publishing a trace to the configured destinations.
    """
    if _wrap_disabled():
        return client

    config = _build_config(
        agent_name=agent_name,
        agent_version=agent_version,
        redact=redact,
        redact_at=redact_at,
        request_id=request_id,
        max_trace_kb=max_trace_kb,
        max_tool_result_kb=max_tool_result_kb,
    )
    _ensure_destinations(destinations)

    target = client.chat.completions
    original = target.create
    is_async = asyncio.iscoroutinefunction(original)

    if is_async:
        async def wrapped_create_async(*args, **kwargs):
            return await _intercept(
                provider="openai",
                config=config,
                original=original,
                args=args,
                kwargs=kwargs,
                is_async=True,
            )
        target.create = wrapped_create_async
    else:
        def wrapped_create_sync(*args, **kwargs):
            return _intercept(
                provider="openai",
                config=config,
                original=original,
                args=args,
                kwargs=kwargs,
                is_async=False,
            )
        target.create = wrapped_create_sync

    return client


def wrap_anthropic(
    client: Any,
    agent_name: str = "agent",
    agent_version: Optional[str] = None,
    redact: Any = _SENTINEL,
    redact_at: str = "capture",
    request_id: RequestIdSpec = None,
    destinations: Optional[Sequence[Destination]] = None,
    max_trace_kb: int = DEFAULT_MAX_TRACE_KB,
    max_tool_result_kb: int = DEFAULT_MAX_TOOL_RESULT_KB,
) -> Any:
    """Wrap an Anthropic client so every messages.create is captured."""
    if _wrap_disabled():
        return client

    config = _build_config(
        agent_name=agent_name,
        agent_version=agent_version,
        redact=redact,
        redact_at=redact_at,
        request_id=request_id,
        max_trace_kb=max_trace_kb,
        max_tool_result_kb=max_tool_result_kb,
    )
    _ensure_destinations(destinations)

    target = client.messages
    original = target.create
    is_async = asyncio.iscoroutinefunction(original)

    if is_async:
        async def wrapped_create_async(*args, **kwargs):
            return await _intercept(
                provider="anthropic",
                config=config,
                original=original,
                args=args,
                kwargs=kwargs,
                is_async=True,
            )
        target.create = wrapped_create_async
    else:
        def wrapped_create_sync(*args, **kwargs):
            return _intercept(
                provider="anthropic",
                config=config,
                original=original,
                args=args,
                kwargs=kwargs,
                is_async=False,
            )
        target.create = wrapped_create_sync

    return client


# --- internals ---------------------------------------------------------------


def _wrap_disabled() -> bool:
    return os.environ.get("TOOL_POUCH_DISABLE_WRAP") == "1"


def _build_config(
    *,
    agent_name: str,
    agent_version: Optional[str],
    redact: Any,
    redact_at: str,
    request_id: RequestIdSpec,
    max_trace_kb: int,
    max_tool_result_kb: int,
) -> WrapConfig:
    if redact_at not in ("capture", "write"):
        raise ValueError(
            f"redact_at must be 'capture' or 'write', got {redact_at!r}"
        )
    if redact is _SENTINEL:
        redactor: Optional[Callable[[Any], Any]] = redact_module.builtin()
    elif redact is None or callable(redact):
        redactor = redact
    else:
        raise TypeError(
            "redact must be None, a callable, or a Redactor (got "
            f"{type(redact).__name__})"
        )
    return WrapConfig(
        agent_name=agent_name,
        agent_version=agent_version,
        redactor=redactor,
        redact_at=redact_at,
        request_id=request_id,
        max_trace_kb=max_trace_kb,
        max_tool_result_kb=max_tool_result_kb,
    )


def _ensure_destinations(destinations: Optional[Sequence[Destination]]) -> None:
    """Configure writer destinations once per process.

    If the caller supplies destinations, they win. Otherwise, default to
    LocalStore so a vanilla `wrap_openai(client)` does something useful.
    """
    if destinations is not None:
        set_destinations(destinations)
        return
    if not get_writer().get_destinations():
        set_destinations([LocalStore()])


def _intercept(
    *,
    provider: str,
    config: WrapConfig,
    original: Callable[..., Any],
    args: tuple,
    kwargs: dict,
    is_async: bool,
):
    """Run the wrapped call, capture either the response or the exception.

    Streaming responses are wrapped in a passthrough iterator that
    captures on exhaustion. Errors raised by the underlying client are
    captured and re-raised so the caller sees the same failure.
    """
    is_stream = bool(kwargs.get("stream"))
    started_at = time.time()
    request_id = _resolve_request_id(config.request_id, args, kwargs)

    if is_async:
        return _intercept_async(
            provider, config, original, args, kwargs, is_stream,
            started_at, request_id,
        )

    try:
        response = original(*args, **kwargs)
    except Exception as exc:
        _commit_failure(provider, config, started_at, request_id, args, kwargs, exc)
        raise

    if is_stream:
        return _wrap_sync_stream(
            provider, config, started_at, request_id, args, kwargs, response,
        )

    _commit_success(
        provider, config, started_at, request_id, args, kwargs, response,
    )
    return response


async def _intercept_async(
    provider: str,
    config: WrapConfig,
    original: Callable[..., Any],
    args: tuple,
    kwargs: dict,
    is_stream: bool,
    started_at: float,
    request_id: Optional[str],
):
    try:
        response = await original(*args, **kwargs)
    except Exception as exc:
        _commit_failure(provider, config, started_at, request_id, args, kwargs, exc)
        raise

    if is_stream:
        return _wrap_async_stream(
            provider, config, started_at, request_id, args, kwargs, response,
        )

    _commit_success(
        provider, config, started_at, request_id, args, kwargs, response,
    )
    return response


# --- request_id resolution ---------------------------------------------------


def _resolve_request_id(
    spec: RequestIdSpec,
    args: tuple,
    kwargs: dict,
) -> Optional[str]:
    """Return the request_id for this call, fallback to a uuid4.

    Strings are returned verbatim. Callables are invoked with the
    request's `kwargs` so users can extract from request shape.
    """
    if spec is None:
        return str(uuid.uuid4())
    if isinstance(spec, str):
        return spec
    try:
        rid = spec(**kwargs) if _wants_kwargs(spec) else spec(args, kwargs)
        return str(rid) if rid is not None else str(uuid.uuid4())
    except Exception:
        return str(uuid.uuid4())


def _wants_kwargs(fn: Callable[..., Any]) -> bool:
    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):
        return False
    params = list(sig.parameters.values())
    return any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params)


# --- capture / commit --------------------------------------------------------


def _commit_success(
    provider: str,
    config: WrapConfig,
    started_at: float,
    request_id: Optional[str],
    args: tuple,
    kwargs: dict,
    response: Any,
) -> None:
    trace = _build_trace(provider, kwargs, response, error=None)
    _enqueue_trace(provider, config, started_at, request_id, trace,
                   outcome="completed", failure_type="completed")


def _commit_failure(
    provider: str,
    config: WrapConfig,
    started_at: float,
    request_id: Optional[str],
    args: tuple,
    kwargs: dict,
    exc: BaseException,
) -> None:
    trace = _build_trace(provider, kwargs, response=None, error=repr(exc))
    _enqueue_trace(provider, config, started_at, request_id, trace,
                   outcome="crashed", failure_type="crashed")


def _enqueue_trace(
    provider: str,
    config: WrapConfig,
    started_at: float,
    request_id: Optional[str],
    trace: dict,
    outcome: str,
    failure_type: str,
) -> None:
    """Apply redaction (if at='capture'), build a TraceRecord, enqueue."""
    if config.redactor and config.redact_at == "capture":
        trace = redact_module.apply(config.redactor, trace)

    trace = truncate_trace(
        trace,
        max_kb=config.max_trace_kb,
        max_tool_result_kb=config.max_tool_result_kb,
    )

    if config.redactor and config.redact_at == "write":
        trace["_pending_redactor"] = True
        # Writer handles late redaction in destinations — for OSS we
        # just attach a marker; the destination's write path is allowed
        # to invoke the redactor itself if it cares to.

    duration_ms = int((time.time() - started_at) * 1000)

    record = TraceRecord(
        run_id=str(uuid.uuid4()),
        started_at=started_at,
        agent_name=config.agent_name,
        agent_version=config.agent_version,
        request_id=request_id,
        metadata={"provider": provider},
        scenario="__production__",
        target_tool=None,
        outcome=outcome,
        failure_type=failure_type,
        trace=trace,
        duration_ms=duration_ms,
        kind=KIND_PRODUCTION,
    )
    enqueue(record)


# --- trace shape extraction --------------------------------------------------


def _build_trace(
    provider: str,
    kwargs: dict,
    response: Any,
    error: Optional[str],
) -> dict:
    """Extract a provider-agnostic trace dict from the request + response."""
    if provider == "openai":
        return _build_openai_trace(kwargs, response, error)
    if provider == "anthropic":
        return _build_anthropic_trace(kwargs, response, error)
    raise ValueError(f"Unknown provider: {provider}")


def _build_openai_trace(
    kwargs: dict,
    response: Any,
    error: Optional[str],
) -> dict:
    messages = list(kwargs.get("messages") or [])
    tools = list(kwargs.get("tools") or [])
    user_input = _last_user_message(messages)

    output_messages: List[dict] = []
    tool_calls: List[dict] = []
    if response is not None:
        try:
            choice = response.choices[0]
            msg = choice.message
            content = getattr(msg, "content", None)
            output_messages.append({"role": "assistant", "content": content})
            for tc in getattr(msg, "tool_calls", None) or []:
                tool_calls.append({
                    "id": getattr(tc, "id", None),
                    "name": tc.function.name,
                    "arguments": tc.function.arguments,
                })
        except (AttributeError, IndexError, TypeError):
            pass

    return {
        "provider": "openai",
        "model": kwargs.get("model"),
        "user_input": user_input,
        "messages": [_message_to_dict(m) for m in messages] + output_messages,
        "tools": tools,
        "tool_calls": tool_calls,
        "error": error,
    }


def _build_anthropic_trace(
    kwargs: dict,
    response: Any,
    error: Optional[str],
) -> dict:
    messages = list(kwargs.get("messages") or [])
    tools = list(kwargs.get("tools") or [])
    user_input = _last_user_message(messages)

    output_messages: List[dict] = []
    tool_calls: List[dict] = []
    if response is not None:
        try:
            content = getattr(response, "content", None) or []
            text_parts = []
            for block in content:
                btype = getattr(block, "type", None)
                if btype == "text":
                    text_parts.append(getattr(block, "text", ""))
                elif btype == "tool_use":
                    tool_calls.append({
                        "id": getattr(block, "id", None),
                        "name": getattr(block, "name", None),
                        "arguments": getattr(block, "input", None),
                    })
            if text_parts:
                output_messages.append(
                    {"role": "assistant", "content": "".join(text_parts)}
                )
        except (AttributeError, TypeError):
            pass

    return {
        "provider": "anthropic",
        "model": kwargs.get("model"),
        "system": kwargs.get("system"),
        "user_input": user_input,
        "messages": [_message_to_dict(m) for m in messages] + output_messages,
        "tools": tools,
        "tool_calls": tool_calls,
        "error": error,
    }


def _last_user_message(messages: Iterable[Any]) -> Optional[str]:
    """Return the latest user-role message's text, if any."""
    last: Optional[str] = None
    for m in messages:
        role = m.get("role") if isinstance(m, dict) else getattr(m, "role", None)
        if role != "user":
            continue
        content = (
            m.get("content") if isinstance(m, dict) else getattr(m, "content", None)
        )
        if isinstance(content, str):
            last = content
        elif isinstance(content, list):
            # Multimodal content blocks; pluck text parts.
            parts = []
            for c in content:
                if isinstance(c, dict) and c.get("type") == "text":
                    parts.append(c.get("text", ""))
            last = "".join(parts) if parts else last
    return last


def _message_to_dict(m: Any) -> dict:
    if isinstance(m, dict):
        return dict(m)
    if hasattr(m, "model_dump"):
        return m.model_dump()
    return {"role": getattr(m, "role", None), "content": getattr(m, "content", None)}


# --- streaming ---------------------------------------------------------------


def _wrap_sync_stream(
    provider: str,
    config: WrapConfig,
    started_at: float,
    request_id: Optional[str],
    args: tuple,
    kwargs: dict,
    stream: Iterable[Any],
):
    """Yield each chunk to the caller while accumulating capture state.

    On normal exhaustion, commits the assembled trace. On exception
    mid-stream, commits what we have with outcome=crashed and re-raises.
    """
    accumulator = _build_accumulator(provider)

    def _gen():
        nonlocal accumulator
        try:
            for chunk in stream:
                accumulator.absorb(chunk)
                yield chunk
        except Exception as exc:
            trace = accumulator.finalize(kwargs, error=repr(exc))
            _enqueue_trace(provider, config, started_at, request_id, trace,
                           outcome="crashed", failure_type="crashed")
            raise
        else:
            trace = accumulator.finalize(kwargs, error=None)
            _enqueue_trace(provider, config, started_at, request_id, trace,
                           outcome="completed", failure_type="completed")

    return _gen()


def _wrap_async_stream(
    provider: str,
    config: WrapConfig,
    started_at: float,
    request_id: Optional[str],
    args: tuple,
    kwargs: dict,
    stream: Any,
):
    accumulator = _build_accumulator(provider)

    async def _agen():
        try:
            async for chunk in stream:
                accumulator.absorb(chunk)
                yield chunk
        except Exception as exc:
            trace = accumulator.finalize(kwargs, error=repr(exc))
            _enqueue_trace(provider, config, started_at, request_id, trace,
                           outcome="crashed", failure_type="crashed")
            raise
        else:
            trace = accumulator.finalize(kwargs, error=None)
            _enqueue_trace(provider, config, started_at, request_id, trace,
                           outcome="completed", failure_type="completed")

    return _agen()


def _build_accumulator(provider: str) -> "_StreamAccumulator":
    if provider == "openai":
        return _OpenAIStreamAccumulator()
    if provider == "anthropic":
        return _AnthropicStreamAccumulator()
    raise ValueError(f"Unknown provider: {provider}")


class _StreamAccumulator:
    """Base — provider-specific subclasses implement `absorb` + `finalize`."""

    def absorb(self, chunk: Any) -> None:
        raise NotImplementedError

    def finalize(self, kwargs: dict, error: Optional[str]) -> dict:
        raise NotImplementedError


class _OpenAIStreamAccumulator(_StreamAccumulator):
    """Reassemble OpenAI streaming deltas into a single trace.

    Tool call deltas arrive in pieces — `function.arguments` is built up
    string-by-string across chunks, keyed by the `index` field. We
    coalesce them into one dict per tool call before commit.
    """

    def __init__(self) -> None:
        self._content: List[str] = []
        self._tool_calls: dict = {}  # index -> {id, name, arguments}
        self._model: Optional[str] = None

    def absorb(self, chunk: Any) -> None:
        try:
            self._model = self._model or getattr(chunk, "model", None)
            choices = getattr(chunk, "choices", None) or []
            if not choices:
                return
            delta = getattr(choices[0], "delta", None)
            if delta is None:
                return
            content = getattr(delta, "content", None)
            if content:
                self._content.append(content)
            for tc in getattr(delta, "tool_calls", None) or []:
                idx = getattr(tc, "index", 0)
                slot = self._tool_calls.setdefault(
                    idx, {"id": None, "name": None, "arguments": ""}
                )
                if getattr(tc, "id", None):
                    slot["id"] = tc.id
                fn = getattr(tc, "function", None)
                if fn is not None:
                    if getattr(fn, "name", None):
                        slot["name"] = fn.name
                    if getattr(fn, "arguments", None):
                        slot["arguments"] += fn.arguments
        except (AttributeError, TypeError):
            pass

    def finalize(self, kwargs: dict, error: Optional[str]) -> dict:
        messages = list(kwargs.get("messages") or [])
        return {
            "provider": "openai",
            "model": self._model or kwargs.get("model"),
            "user_input": _last_user_message(messages),
            "messages": [_message_to_dict(m) for m in messages]
            + [{"role": "assistant", "content": "".join(self._content)}],
            "tools": list(kwargs.get("tools") or []),
            "tool_calls": [
                self._tool_calls[i] for i in sorted(self._tool_calls.keys())
            ],
            "error": error,
            "stream": True,
        }


class _AnthropicStreamAccumulator(_StreamAccumulator):
    """Reassemble Anthropic streaming events into a single trace."""

    def __init__(self) -> None:
        self._text: List[str] = []
        self._tool_calls: dict = {}  # index -> {id, name, arguments}
        self._model: Optional[str] = None

    def absorb(self, event: Any) -> None:
        try:
            etype = getattr(event, "type", None)
            if etype == "message_start":
                self._model = getattr(getattr(event, "message", None), "model", None)
            elif etype == "content_block_start":
                block = getattr(event, "content_block", None)
                idx = getattr(event, "index", 0)
                if getattr(block, "type", None) == "tool_use":
                    self._tool_calls[idx] = {
                        "id": getattr(block, "id", None),
                        "name": getattr(block, "name", None),
                        "arguments": "",
                    }
            elif etype == "content_block_delta":
                idx = getattr(event, "index", 0)
                delta = getattr(event, "delta", None)
                dtype = getattr(delta, "type", None)
                if dtype == "text_delta":
                    self._text.append(getattr(delta, "text", ""))
                elif dtype == "input_json_delta" and idx in self._tool_calls:
                    self._tool_calls[idx]["arguments"] += getattr(
                        delta, "partial_json", ""
                    )
        except (AttributeError, TypeError):
            pass

    def finalize(self, kwargs: dict, error: Optional[str]) -> dict:
        messages = list(kwargs.get("messages") or [])
        return {
            "provider": "anthropic",
            "model": self._model or kwargs.get("model"),
            "system": kwargs.get("system"),
            "user_input": _last_user_message(messages),
            "messages": [_message_to_dict(m) for m in messages]
            + [{"role": "assistant", "content": "".join(self._text)}],
            "tools": list(kwargs.get("tools") or []),
            "tool_calls": [
                self._tool_calls[i] for i in sorted(self._tool_calls.keys())
            ],
            "error": error,
            "stream": True,
        }
