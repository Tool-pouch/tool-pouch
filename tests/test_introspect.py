"""Function -> tool spec introspection."""
from typing import Optional

from tool_pouch._introspect import normalize, to_anthropic, to_openai, to_spec


def search(q: str, limit: int = 10) -> dict:
    """Search the web for q.

    Longer description that should be ignored.
    """
    return {}


def _undocumented(x: str) -> dict:
    return {}


def maybe_fetch(url: Optional[str] = None) -> dict:
    """Fetch the URL if provided."""
    return {}


def test_basic_spec():
    spec = to_spec(search)
    assert spec.name == "search"
    assert spec.description == "Search the web for q"
    assert spec.required == ["q"]
    assert spec.properties == {
        "q": {"type": "string"},
        "limit": {"type": "integer"},
    }


def test_optional_param_not_required():
    spec = to_spec(maybe_fetch)
    assert spec.required == []
    assert spec.properties["url"]["type"] == "string"


def test_undocumented_function_gets_safe_description():
    spec = to_spec(_undocumented)
    assert spec.description == "Call _undocumented"


def test_openai_render():
    spec = to_spec(search)
    rendered = to_openai(spec)
    assert rendered["type"] == "function"
    assert rendered["function"]["name"] == "search"
    assert rendered["function"]["parameters"]["required"] == ["q"]


def test_anthropic_render():
    spec = to_spec(search)
    rendered = to_anthropic(spec)
    assert rendered["name"] == "search"
    assert rendered["input_schema"]["required"] == ["q"]
    assert "type" not in rendered  # different shape than openai


def test_normalize_accepts_callables_and_specs():
    spec = to_spec(search)
    result = normalize([search, spec])
    assert len(result) == 2
    assert result[0].name == result[1].name == "search"
