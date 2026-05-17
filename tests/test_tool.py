"""@tool_pouch.tool decorator + module-level registry."""
import pytest

from tool_pouch import tool
from tool_pouch.tool import clear, is_tool, registered


@pytest.fixture(autouse=True)
def reset_registry():
    clear()
    yield
    clear()


def test_bare_decorator_marks_and_registers():
    @tool
    def search(q: str) -> dict:
        """Search."""
        return {}

    assert is_tool(search)
    assert search in registered()
    assert search.__tool_pouch_tool__["description"] is None


def test_decorator_with_metadata():
    @tool(description="Search w/ caching", tags=["network", "read"])
    def search(q: str) -> dict:
        return {}

    assert search.__tool_pouch_tool__["description"] == "Search w/ caching"
    assert search.__tool_pouch_tool__["tags"] == ["network", "read"]


def test_decorator_is_identity_at_runtime():
    @tool
    def add(a: int, b: int) -> int:
        return a + b

    assert add(2, 3) == 5


def test_double_decoration_is_idempotent():
    @tool
    def search(q: str) -> dict:
        return {}

    again = tool(search)
    assert registered().count(search) == 1
    assert again is search


def test_non_callable_rejected():
    with pytest.raises(TypeError):
        tool("not a function")  # type: ignore[arg-type]
