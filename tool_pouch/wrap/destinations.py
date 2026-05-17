"""Destinations for captured production traces.

Three destinations ship in OSS:

    LocalStore    SQLite at ~/.tool_pouch/tool_pouch.db. Dev/staging.
    JSONLogger    NDJSON to a stream (default stderr). Prod default until
                  cloud ships — pipes into existing log aggregation
                  (Datadog, Honeycomb, Loki, CloudWatch, etc.).
    HTTPSink      Batched POST to a user-supplied URL. Custom backends.

A future `CloudStore` will be a fourth destination after the Tool Pouch cloud
ships; the wrap API stays unchanged.

All destinations run on the background writer thread. The destination
contract is intentionally small:

    write(trace_record) -> None
    close(timeout) -> None
"""
from __future__ import annotations

import json
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable

from tool_pouch.store import KIND_PRODUCTION, Store


@dataclass
class TraceRecord:
    """One captured request, ready to be written.

    Mirrors the shape of (runs, results) so destinations can either
    write structured rows (LocalStore) or serialize the whole record
    (JSONLogger / HTTPSink). The writer thread builds these from the
    proxy's raw capture payload.
    """

    run_id: str
    started_at: float
    agent_name: str
    agent_version: Optional[str]
    request_id: Optional[str]
    metadata: Dict[str, Any]
    scenario: str
    target_tool: Optional[str]
    outcome: str
    failure_type: str
    trace: Dict[str, Any]
    duration_ms: int
    kind: str = KIND_PRODUCTION

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@runtime_checkable
class Destination(Protocol):
    """The contract every destination implements."""

    def write(self, record: TraceRecord) -> None:
        """Persist a single trace. Called from the writer thread."""

    def close(self, timeout: float = 2.0) -> None:
        """Flush in-flight buffers; called on flush() / atexit."""


class LocalStore:
    """Writes traces to the local SQLite Store at ~/.tool_pouch/tool_pouch.db.

    The default destination. Designed for dev/staging — production users
    should switch to JSONLogger or HTTPSink because container restarts
    erase ~/.tool_pouch/tool_pouch.db on ephemeral filesystems.
    """

    def __init__(self, store: Optional[Store] = None):
        # Lazy default so tests with $TOOL_POUCH_DB don't need to inject a
        # store. The first wrap on a process opens the SQLite file.
        self._store = store

    def _ensure_store(self) -> Store:
        if self._store is None:
            self._store = Store()
        return self._store

    def write(self, record: TraceRecord) -> None:
        store = self._ensure_store()
        # The wrap captures one logical request per call, so we create
        # one run + one result. Metadata carries request_id and any
        # other capture-side annotations.
        merged_metadata = dict(record.metadata)
        if record.request_id is not None:
            merged_metadata["request_id"] = record.request_id

        run_id = store.new_run(
            agent_name=record.agent_name,
            user_input=record.trace.get("user_input"),
            agent_version=record.agent_version,
            environment="production",
            metadata=merged_metadata,
            kind=record.kind,
        )
        store.add_result(
            run_id=run_id,
            scenario=record.scenario,
            target_tool=record.target_tool,
            outcome=record.outcome,
            failure_type=record.failure_type,
            trace=record.trace,
            duration_ms=record.duration_ms,
        )

    def close(self, timeout: float = 2.0) -> None:
        # SQLite commits per-row already; nothing to flush.
        return None


class JSONLogger:
    """Emits one NDJSON line per trace to a writable stream.

    Recommended production destination until Tool Pouch Cloud ships. The
    customer's existing log pipeline (Datadog Agent, Vector, Fluent Bit,
    CloudWatch Logs) is responsible for persistence and search.

    Threadsafe: writes are guarded by a lock so concurrent calls produce
    well-formed NDJSON instead of interleaved partial lines.
    """

    def __init__(self, stream=None, format: str = "ndjson"):
        if format != "ndjson":
            raise ValueError(f"Unsupported format: {format!r}; only 'ndjson'")
        self._stream = stream if stream is not None else sys.stderr
        self._lock = threading.Lock()

    def write(self, record: TraceRecord) -> None:
        line = json.dumps(record.to_dict(), default=_json_default)
        with self._lock:
            self._stream.write(line + "\n")
            self._stream.flush()

    def close(self, timeout: float = 2.0) -> None:
        with self._lock:
            try:
                self._stream.flush()
            except Exception:
                pass


@dataclass
class _PendingBatch:
    items: List[Dict[str, Any]] = field(default_factory=list)
    last_flush_at: float = field(default_factory=time.monotonic)


class HTTPSink:
    """POSTs batched traces to a user-supplied URL.

    Batches by count (default 10) or time (default 5s), whichever first.
    Retries with exponential backoff on 5xx / connection errors; drops
    on persistent failure (never blocks the writer thread).

    Useful for customers piping traces into their own backend, or for
    plugging into in-house observability platforms.
    """

    def __init__(
        self,
        url: str,
        headers: Optional[Dict[str, str]] = None,
        batch_size: int = 10,
        flush_interval_s: float = 5.0,
        max_retries: int = 3,
        timeout_s: float = 5.0,
    ):
        self.url = url
        self.headers = {"Content-Type": "application/json", **(headers or {})}
        self.batch_size = max(1, batch_size)
        self.flush_interval_s = flush_interval_s
        self.max_retries = max_retries
        self.timeout_s = timeout_s

        self._lock = threading.Lock()
        self._batch = _PendingBatch()

    def write(self, record: TraceRecord) -> None:
        item = record.to_dict()
        with self._lock:
            self._batch.items.append(item)
            should_flush = (
                len(self._batch.items) >= self.batch_size
                or (time.monotonic() - self._batch.last_flush_at)
                >= self.flush_interval_s
            )
            to_send = self._take_batch() if should_flush else None
        if to_send:
            self._post(to_send)

    def close(self, timeout: float = 2.0) -> None:
        with self._lock:
            to_send = self._take_batch() if self._batch.items else None
        if to_send:
            self._post(to_send)

    def _take_batch(self) -> List[Dict[str, Any]]:
        items = self._batch.items
        self._batch = _PendingBatch()
        return items

    def _post(self, items: List[Dict[str, Any]]) -> None:
        body = json.dumps({"traces": items}, default=_json_default).encode("utf-8")
        for attempt in range(self.max_retries + 1):
            req = urllib.request.Request(
                self.url, data=body, headers=self.headers, method="POST"
            )
            try:
                with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                    if 200 <= resp.status < 300:
                        return
                    if 400 <= resp.status < 500:
                        # Client errors are fatal; retrying won't help.
                        return
            except (urllib.error.URLError, TimeoutError, ConnectionError):
                pass
            time.sleep(min(2**attempt * 0.1, 2.0))


def _json_default(obj: Any) -> Any:
    """Render uncommon types as JSON without crashing the writer.

    Falls back to `repr()` for anything we don't know how to serialize.
    The writer thread must never raise from a write — a misbehaving
    user payload should not stop captures from flowing.
    """
    try:
        if hasattr(obj, "model_dump"):
            return obj.model_dump()
        if hasattr(obj, "to_dict"):
            return obj.to_dict()
    except Exception:
        pass
    return repr(obj)
