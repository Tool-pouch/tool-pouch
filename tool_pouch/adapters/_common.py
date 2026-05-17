"""Shared plumbing for framework adapters."""
from __future__ import annotations

from typing import Any, Callable, Dict, List

from tool_pouch._introspect import ToolSpec, normalize


class ToolDispatcher:
    """Wraps the user's tool callables so the runner sees the (name, args)
    interface it already understands.
    """

    def __init__(self, specs: List[ToolSpec]):
        self._by_name: Dict[str, Callable[..., Any]] = {s.name: s.fn for s in specs}

    @property
    def names(self) -> List[str]:
        return list(self._by_name.keys())

    def call(self, name: str, args: Dict[str, Any]) -> Any:
        fn = self._by_name.get(name)
        if fn is None:
            raise KeyError(f"unknown tool: {name}")
        return fn(**args)


def build_dispatcher(tools: List[Any]) -> tuple[ToolDispatcher, List[ToolSpec]]:
    """Normalize tool inputs and return (dispatcher, specs)."""
    specs = normalize(tools)
    return ToolDispatcher(specs), specs
