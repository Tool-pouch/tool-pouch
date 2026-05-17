"""Background writer thread: enqueue, drain, fail-open, flush, overhead."""
import io
import threading
import time

from tool_pouch.store import KIND_PRODUCTION
from tool_pouch.wrap import flush
from tool_pouch.wrap.destinations import JSONLogger, TraceRecord
from tool_pouch.wrap.writer import _WriterThread


def _record(run_id: str = "r") -> TraceRecord:
    return TraceRecord(
        run_id=run_id,
        started_at=time.time(),
        agent_name="agent",
        agent_version="0.1",
        request_id=None,
        metadata={},
        scenario="__production__",
        target_tool=None,
        outcome="completed",
        failure_type="completed",
        trace={"user_input": "x"},
        duration_ms=1,
        kind=KIND_PRODUCTION,
    )


def test_enqueue_drains_to_destination():
    writer = _WriterThread()
    buf = io.StringIO()
    writer.add_destination(JSONLogger(stream=buf))

    for i in range(5):
        writer.enqueue(_record(run_id=f"r{i}"))
    assert writer.flush(timeout=2.0)

    lines = [line for line in buf.getvalue().strip().split("\n") if line]
    assert len(lines) == 5


def test_destination_failure_does_not_kill_writer():
    """A misbehaving destination must not stop subsequent writes."""

    class _Boom:
        calls = 0

        def write(self, record):
            type(self).calls += 1
            raise RuntimeError("boom")

        def close(self, timeout=2.0):
            return None

    writer = _WriterThread()
    buf = io.StringIO()
    writer.add_destination(_Boom())
    writer.add_destination(JSONLogger(stream=buf))

    writer.enqueue(_record("a"))
    writer.enqueue(_record("b"))
    assert writer.flush(timeout=2.0)
    # Both records still landed in the JSONLogger after the Boom raised.
    assert len(buf.getvalue().strip().split("\n")) == 2
    assert _Boom.calls == 2


def test_flush_returns_true_when_idle():
    writer = _WriterThread()
    # Never started; flush should return True without waiting.
    start = time.monotonic()
    assert writer.flush(timeout=1.0)
    assert time.monotonic() - start < 0.1


def test_enqueue_overhead_under_one_ms():
    """The wrap proxy budgets <1ms for enqueue. Sample 100 puts."""
    writer = _WriterThread()
    writer.add_destination(JSONLogger(stream=io.StringIO()))

    samples = []
    for _ in range(200):
        rec = _record()
        t0 = time.perf_counter()
        writer.enqueue(rec)
        samples.append(time.perf_counter() - t0)

    samples.sort()
    p99 = samples[int(len(samples) * 0.99) - 1]
    # Loose bound — CI variance; the spec calls for sub-ms median, p99 < 1ms.
    assert p99 < 0.001, f"enqueue p99={p99*1e6:.1f}us exceeds 1ms"


def test_flush_module_level_when_no_writes_yet():
    """`tool_pouch.flush()` must be safe to call even before any wrap has run."""
    assert flush(timeout=0.1) is True


def test_writer_thread_is_daemon():
    """Daemon thread won't block process exit."""
    writer = _WriterThread()
    writer.add_destination(JSONLogger(stream=io.StringIO()))
    writer.enqueue(_record())

    # Wait for the thread to actually start.
    time.sleep(0.05)
    assert writer._thread is not None
    assert writer._thread.daemon is True


def test_concurrent_enqueue_from_threads():
    writer = _WriterThread()
    buf = io.StringIO()
    writer.add_destination(JSONLogger(stream=buf))

    def producer(label: str):
        for i in range(50):
            writer.enqueue(_record(run_id=f"{label}-{i}"))

    threads = [threading.Thread(target=producer, args=(c,)) for c in "abcd"]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert writer.flush(timeout=5.0)
    lines = [line for line in buf.getvalue().strip().split("\n") if line]
    assert len(lines) == 200
