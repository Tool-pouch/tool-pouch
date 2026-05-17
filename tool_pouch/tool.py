"""Lightweight @tool_pouch.tool decorator + module-level registry.

Usage:

    from tool_pouch import tool

    @tool
    def search(q: str) -> dict:
        '''Search the web for q.'''
        return ...

The decorator is a transparent identity at runtime — your code keeps
working as before. It only attaches a `__tool_pouch_tool__` marker so
`tool_pouch.discover()` and the CLI can find it without import-time scanning
of every callable.

Optional metadata:

    @tool(description="Search w/ caching", tags=["network", "read"])
    def search(...): ...

Future: the same decorator is the hook for the paid wrapper that ships
prod traces to tool-pouch cloud (`__tool_pouch_tool__["project"]`, etc.).
"""
from __future__ import annotations

import inspect
from typing import Any, Callable, List, Optional


_REGISTRY: List[Callable[..., Any]] = []


def tool(
    fn: Optional[Callable[..., Any]] = None,
    *,
    description: Optional[str] = None,
    tags: Optional[List[str]] = None,
) -> Callable[..., Any]:
    """Mark a function as a tool-pouch tool. Works as `@tool` or `@tool(...)`."""

    def _wrap(f: Callable[..., Any]) -> Callable[..., Any]:
        if not callable(f):
            raise TypeError("@tool_pouch.tool only decorates callables")
        f.__tool_pouch_tool__ = {  # type: ignore[attr-defined]
            "description": description,
            "tags": list(tags) if tags else [],
            "module": getattr(f, "__module__", None),
            "qualname": getattr(f, "__qualname__", f.__name__),
        }
        if f not in _REGISTRY:
            _REGISTRY.append(f)
        return f

    if fn is None:
        return _wrap
    return _wrap(fn)


def is_tool(fn: Any) -> bool:
    return callable(fn) and hasattr(fn, "__tool_pouch_tool__")


def registered() -> List[Callable[..., Any]]:
    """Return a snapshot of all currently-registered tools."""
    return list(_REGISTRY)


def clear() -> None:
    """Reset the registry. Used by tests; not part of the public API."""
    _REGISTRY.clear()


def from_module(mod: Any) -> List[Callable[..., Any]]:
    """Return @tool-decorated callables defined in `mod`.

    Used by `tool_pouch.discover` after importing each scanned file.
    """
    found: List[Callable[..., Any]] = []
    for _, obj in inspect.getmembers(mod):
        if is_tool(obj) and getattr(obj, "__module__", None) == mod.__name__:
            found.append(obj)
    return found
