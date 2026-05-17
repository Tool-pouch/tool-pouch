"""Convert a Python callable into the JSON-schema shape that OpenAI and
Anthropic tool-calling APIs expect.

Kept intentionally minimal: covers str / int / float / bool / list / dict and
their typing.* generics. Falls back to "string" for anything exotic. Users
who need richer schemas (TypedDict, Pydantic, enums) can always pass
explicit tool specs to the adapter.
"""
from __future__ import annotations

import inspect
import typing
from dataclasses import dataclass
from typing import Any, Callable, Dict, List


_PY_TO_JSON: Dict[Any, str] = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
    list: "array",
    dict: "object",
}


@dataclass(frozen=True)
class ToolSpec:
    """Normalized representation of a tool. Both adapters render from this."""

    name: str
    description: str
    properties: Dict[str, Dict[str, Any]]
    required: List[str]
    fn: Callable[..., Any]


def _json_type_for(py_type: Any) -> str:
    if py_type in _PY_TO_JSON:
        return _PY_TO_JSON[py_type]
    origin = typing.get_origin(py_type)
    if origin in _PY_TO_JSON:
        return _PY_TO_JSON[origin]
    if origin is typing.Union:
        # Optional[T] becomes Union[T, None]; pick the first non-None type.
        non_none = [a for a in typing.get_args(py_type) if a is not type(None)]
        return _json_type_for(non_none[0]) if non_none else "string"
    return "string"


def _short_description(fn: Callable[..., Any]) -> str:
    """First non-empty line of the docstring, without trailing period.

    Tool descriptions show up in model prompts; long multi-paragraph
    docstrings are noise. The first line is the part the model reads.
    """
    doc = inspect.getdoc(fn) or ""
    for line in doc.splitlines():
        line = line.strip()
        if line:
            return line.rstrip(".")
    return f"Call {fn.__name__}"


def to_spec(fn: Callable[..., Any]) -> ToolSpec:
    """Inspect a Python callable and return the normalized tool spec."""
    sig = inspect.signature(fn)
    try:
        hints = typing.get_type_hints(fn)
    except Exception:
        hints = {}

    properties: Dict[str, Dict[str, Any]] = {}
    required: List[str] = []

    for param_name, param in sig.parameters.items():
        if param.kind in (param.VAR_POSITIONAL, param.VAR_KEYWORD):
            continue
        py_type = hints.get(param_name, str)
        properties[param_name] = {"type": _json_type_for(py_type)}
        if param.default is inspect.Parameter.empty:
            required.append(param_name)

    return ToolSpec(
        name=fn.__name__,
        description=_short_description(fn),
        properties=properties,
        required=required,
        fn=fn,
    )


def normalize(tools: List[Any]) -> List[ToolSpec]:
    """Accept callables and pre-built ToolSpecs interchangeably."""
    out: List[ToolSpec] = []
    for t in tools:
        if isinstance(t, ToolSpec):
            out.append(t)
        elif callable(t):
            out.append(to_spec(t))
        else:
            raise TypeError(
                f"tools[] entries must be callables or ToolSpec, got {type(t).__name__}"
            )
    return out


def to_openai(spec: ToolSpec) -> Dict[str, Any]:
    """Render a ToolSpec as an OpenAI chat-completions tool definition."""
    return {
        "type": "function",
        "function": {
            "name": spec.name,
            "description": spec.description,
            "parameters": {
                "type": "object",
                "properties": spec.properties,
                "required": spec.required,
            },
        },
    }


def to_anthropic(spec: ToolSpec) -> Dict[str, Any]:
    """Render a ToolSpec as an Anthropic Messages API tool definition."""
    return {
        "name": spec.name,
        "description": spec.description,
        "input_schema": {
            "type": "object",
            "properties": spec.properties,
            "required": spec.required,
        },
    }
