"""Background writer thread for production trace capture.

The contract:

    enqueue(record)   sub-millisecond, never blocks the request path
    flush(timeout)    drains the queue, returns when empty or timed out

Internals:

    A single daemon Thread per process pulls from `_queue`, runs each
    record's destination(s) in turn, and never raises into the caller.
    On fork, child processes get a fresh queue + thread (handled by
    `os.register_at_fork`) so trace capture survives gunicorn / uvicorn
    pre-fork model deployments.

    `atexit` registers `flush()` so traces enqueued at the very end of
    a process get a chance to land before exit.

Failure model — fail-open:

    If a destination raises, the writer logs to stderr and continues.
    The wrap proxy must always pass the original LLM response through;
    capture failures do NOT propagate.
"""
from __future__ import annotations

import atexit
import os
import queue
import sys
import threading
import time
from typing import List, Optional, Sequence

from tool_pouch.wrap.destinations import Destination, TraceRecord


_QUEUE_SIZE = 10_000
_SENTINEL = object()


class _WriterThread:
    """Owns the queue and the background drainer thread for this process."""

    def __init__(self) -> None:
        self._queue: queue.Queue = queue.Queue(maxsize=_QUEUE_SIZE)
        self._destinations: List[Destination] = []
        self._destinations_lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._started = False
        self._start_lock = threading.Lock()

    def add_destination(self, destination: Destination) -> None:
        with self._destinations_lock:
            self._destinations.append(destination)

    def get_destinations(self) -> List[Destination]:
        with self._destinations_lock:
            return list(self._destinations)

    def enqueue(self, record: TraceRecord) -> None:
        """Queue a record for the writer thread.

        Non-blocking. If the queue is full (writer thread can't keep up),
        the record is dropped — never block the request path. This is
        the single most important invariant of the wrap system.
        """
        self._ensure_started()
        try:
            self._queue.put_nowait(record)
        except queue.Full:
            _log_warn("tool-pouch: writer queue full, dropping trace")

    def _ensure_started(self) -> None:
        if self._started:
            return
        with self._start_lock:
            if self._started:
                return
            self._thread = threading.Thread(
                target=self._drain_loop,
                name="tool-pouch-wrap-writer",
                daemon=True,
            )
            self._thread.start()
            self._started = True

    def _drain_loop(self) -> None:
        while True:
            item = self._queue.get()
            try:
                if item is _SENTINEL:
                    return
                self._dispatch(item)
            finally:
                self._queue.task_done()

    def _dispatch(self, record: TraceRecord) -> None:
        for dest in self.get_destinations():
            try:
                dest.write(record)
            except Exception as exc:  # noqa: BLE001 — fail-open
                _log_warn(f"tool-pouch: destination {type(dest).__name__} failed: {exc!r}")

    def flush(self, timeout: float = 2.0) -> bool:
        """Drain the queue and flush every destination.

        Returns True if everything drained within `timeout`, False if
        the deadline hit first. We wait for `task_done()` (not just
        `queue.empty()`) so an in-flight record on the writer thread
        isn't truncated when the caller exits.
        """
        if not self._started:
            return True
        deadline = time.monotonic() + max(0.0, timeout)

        # Wait until every enqueued task is fully processed. Polling
        # because Queue.join doesn't accept a timeout — but an internal
        # check on `unfinished_tasks` does.
        while self._queue.unfinished_tasks > 0:
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.005)

        # Flush each destination's internal buffers.
        remaining = max(0.0, deadline - time.monotonic())
        for dest in self.get_destinations():
            try:
                dest.close(timeout=remaining)
            except Exception as exc:  # noqa: BLE001
                _log_warn(f"tool-pouch: destination {type(dest).__name__} close failed: {exc!r}")
        return True

    def set_destinations(self, destinations: Sequence[Destination]) -> None:
        with self._destinations_lock:
            self._destinations = list(destinations)

    def reset_for_fork(self) -> None:
        """After fork: rebuild every per-process resource in the child.

        Inherited Lock objects can be held by ghost threads from the
        parent; the only safe move is to drop them. Destinations stay
        because they're typically process-safe (file paths, URLs).
        """
        survived = list(self._destinations)
        self._queue = queue.Queue(maxsize=_QUEUE_SIZE)
        self._thread = None
        self._started = False
        self._start_lock = threading.Lock()
        self._destinations_lock = threading.Lock()
        self._destinations = survived


_writer = _WriterThread()


def get_writer() -> _WriterThread:
    return _writer


def add_destination(destination: Destination) -> None:
    """Register a destination on the global writer."""
    _writer.add_destination(destination)


def set_destinations(destinations: Sequence[Destination]) -> None:
    """Replace the destination list. Used by wrap_* on first call."""
    _writer.set_destinations(destinations)


def enqueue(record: TraceRecord) -> None:
    _writer.enqueue(record)


def flush(timeout: float = 2.0) -> bool:
    """Public API: block until in-flight traces are persisted.

    Call this before process exit for synchronous workloads (CLIs,
    short scripts). Long-running services rely on the atexit hook.
    """
    return _writer.flush(timeout=timeout)


def _log_warn(msg: str) -> None:
    try:
        sys.stderr.write(msg + "\n")
    except Exception:
        pass


def _on_fork() -> None:
    _writer.reset_for_fork()


# Wire fork + exit hooks once at import time. `os.register_at_fork` is
# only available on Unix; on Windows it's a noop, which is fine because
# Windows uses spawn rather than fork.
if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_on_fork)

atexit.register(flush)
