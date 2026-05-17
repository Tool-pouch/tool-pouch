"""Walk a path (file or directory) and collect @pouch.tool callables.

Convention over configuration:
- Single file: import it, return its tools.
- Directory: recursively import every `*.py`, return all tools found.
- Skips the usual noise (__pycache__, .venv, venv, node_modules, .git).

Each scanned file is imported under a unique synthetic module name so
discovery doesn't collide with the user's real package layout.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any, Callable, List, Union

from tool_pouch.tool import from_module


_SKIP_DIRS = {"__pycache__", ".venv", "venv", "node_modules", ".git",
              ".pytest_cache", ".mypy_cache", ".ruff_cache", "dist", "build"}


def discover(path: Union[str, Path]) -> List[Callable[..., Any]]:
    """Return all @pouch.tool callables found at `path`."""
    p = Path(path).expanduser().resolve()

    if not p.exists():
        raise FileNotFoundError(f"discover path does not exist: {p}")

    files = _files_to_scan(p)
    if not files:
        return []

    found: List[Callable[..., Any]] = []
    seen_ids = set()
    for f in files:
        try:
            mod = _import_file(f)
        except Exception as e:
            # Don't let one broken file kill the whole scan; skip with a warning.
            print(f"tool-pouch: skipping {f} ({type(e).__name__}: {e})",
                  file=sys.stderr)
            continue

        for t in from_module(mod):
            if id(t) not in seen_ids:
                seen_ids.add(id(t))
                found.append(t)

    return found


def _files_to_scan(p: Path) -> List[Path]:
    if p.is_file():
        return [p] if p.suffix == ".py" else []
    return sorted(
        f for f in p.rglob("*.py")
        if not _should_skip(f)
    )


def _should_skip(path: Path) -> bool:
    return any(part in _SKIP_DIRS for part in path.parts)


def _import_file(path: Path) -> Any:
    """Import a .py file under a synthetic module name."""
    mod_name = f"_tool_pouch_discover_{path.stem}_{abs(hash(str(path)))}"
    spec = importlib.util.spec_from_file_location(mod_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"could not load spec for {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod
