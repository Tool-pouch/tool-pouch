"""Path-based tool discovery."""
import pytest

from tool_pouch.discover import discover
from tool_pouch.tool import clear


@pytest.fixture(autouse=True)
def reset_registry():
    clear()
    yield
    clear()


SINGLE_FILE = '''
from tool_pouch import tool

@tool
def search(q: str) -> dict:
    """Search."""
    return {}

@tool
def fetch(url: str) -> dict:
    """Fetch."""
    return {}

def helper(x: int) -> int:
    return x + 1
'''

OTHER_FILE = '''
from tool_pouch import tool

@tool
def lookup(key: str) -> dict:
    """Lookup by key."""
    return {}
'''

BROKEN_FILE = '''
import this_module_does_not_exist
'''


def test_single_file(tmp_path):
    f = tmp_path / "tools.py"
    f.write_text(SINGLE_FILE)
    found = discover(f)
    names = sorted(t.__name__ for t in found)
    assert names == ["fetch", "search"]


def test_directory_recursive(tmp_path):
    (tmp_path / "tools.py").write_text(SINGLE_FILE)
    sub = tmp_path / "extra"
    sub.mkdir()
    (sub / "more.py").write_text(OTHER_FILE)

    found = discover(tmp_path)
    names = sorted(t.__name__ for t in found)
    assert names == ["fetch", "lookup", "search"]


def test_skips_known_noise(tmp_path):
    (tmp_path / "tools.py").write_text(SINGLE_FILE)
    venv = tmp_path / ".venv"
    venv.mkdir()
    (venv / "bad.py").write_text(BROKEN_FILE)

    found = discover(tmp_path)
    assert sorted(t.__name__ for t in found) == ["fetch", "search"]


def test_broken_file_is_skipped_not_fatal(tmp_path, capsys):
    (tmp_path / "good.py").write_text(SINGLE_FILE)
    (tmp_path / "bad.py").write_text(BROKEN_FILE)

    found = discover(tmp_path)
    assert sorted(t.__name__ for t in found) == ["fetch", "search"]
    captured = capsys.readouterr()
    assert "bad.py" in captured.err


def test_missing_path_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        discover(tmp_path / "nope")


def test_empty_directory_returns_empty(tmp_path):
    assert discover(tmp_path) == []
