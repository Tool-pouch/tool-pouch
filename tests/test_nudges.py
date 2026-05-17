"""Nudge persistence: shows once per key."""
import io

import pytest

from tool_pouch import nudges


@pytest.fixture
def isolated_state(tmp_path, monkeypatch):
    monkeypatch.setenv("TOOL_POUCH_NUDGES_PATH", str(tmp_path / "nudges.json"))


def test_show_once_first_call_prints(isolated_state):
    buf = io.StringIO()
    assert nudges.show_once("k1", "hello", stream=buf) is True
    assert "hello" in buf.getvalue()


def test_show_once_second_call_silent(isolated_state):
    buf = io.StringIO()
    nudges.show_once("k1", "hi", stream=buf)
    buf2 = io.StringIO()
    assert nudges.show_once("k1", "hi", stream=buf2) is False
    assert buf2.getvalue() == ""


def test_show_once_distinct_keys_independent(isolated_state):
    buf = io.StringIO()
    assert nudges.show_once("a", "msg-a", stream=buf) is True
    assert nudges.show_once("b", "msg-b", stream=buf) is True


def test_reset_clears_one_key(isolated_state):
    buf = io.StringIO()
    nudges.show_once("k", "first", stream=buf)
    nudges.reset("k")
    buf2 = io.StringIO()
    assert nudges.show_once("k", "again", stream=buf2) is True


def test_reset_all(isolated_state):
    nudges.show_once("a", "x")
    nudges.show_once("b", "y")
    nudges.reset()
    assert not nudges.has_shown("a")
    assert not nudges.has_shown("b")


def test_corrupt_state_file_is_recovered(tmp_path, monkeypatch):
    path = tmp_path / "nudges.json"
    path.write_text("not json {")
    monkeypatch.setenv("TOOL_POUCH_NUDGES_PATH", str(path))
    buf = io.StringIO()
    assert nudges.show_once("k", "hi", stream=buf) is True
