"""ANSI color helpers.

Auto-disables when:
  - stdout is not a TTY (piped, redirected, in CI without a terminal)
  - NO_COLOR env var is set (https://no-color.org)
  - FORCE_COLOR=0

Respects FORCE_COLOR=1 to enable even when not a TTY (useful for CI logs
that interpret ANSI).
"""
import os
import sys


def _enabled():
    if os.environ.get("NO_COLOR"):
        return False
    fc = os.environ.get("FORCE_COLOR")
    if fc == "0":
        return False
    if fc and fc != "0":
        return True
    return sys.stdout.isatty()


_ON = _enabled()


# ANSI codes
_GREEN = "\033[38;5;114m"   # matches the brand green-ish on most terminals
_RED = "\033[38;5;203m"     # warm red for failures
_YELLOW = "\033[38;5;214m"  # for warnings / "unclear"
_DIM = "\033[2m"
_BOLD = "\033[1m"
_RESET = "\033[0m"


def green(s):
    return f"{_GREEN}{s}{_RESET}" if _ON else s


def red(s):
    return f"{_RED}{s}{_RESET}" if _ON else s


def yellow(s):
    return f"{_YELLOW}{s}{_RESET}" if _ON else s


def dim(s):
    return f"{_DIM}{s}{_RESET}" if _ON else s


def bold(s):
    return f"{_BOLD}{s}{_RESET}" if _ON else s


def color_for_outcome(failure_type):
    """Map a failure type to its display color."""
    bad = {"crashed", "looped", "gave_up", "hallucinated", "silent_wrong", "timeout"}
    if failure_type in bad:
        return red
    if failure_type == "handled":
        return green
    return yellow  # unclear, completed, anything else
