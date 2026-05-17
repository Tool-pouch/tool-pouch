"""PII redaction for captured traces.

Two layers:

1. `builtin()` returns a Redactor that recognises common PII shapes
   (email, phone, SSN, credit card, IPv4/IPv6, common API keys).
2. Custom patterns can be appended via `builtin(extra_patterns=[...])`.

A Redactor is callable:

    redactor(value)  ->  value with strings scrubbed

It walks dicts, lists, tuples, and strings. Anything else is passed
through unchanged (numbers, bools, None, custom objects).

Two redact-time modes are exposed by `tool_pouch.wrap_*()`:

    redact_at='capture'  (default, safe)  scrub on the request thread
                                          before the trace ever queues
    redact_at='write'                     scrub on the writer thread
                                          (slightly faster wrap path,
                                          PII briefly in queue memory)
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, List, Optional, Pattern


REDACTION_TOKEN = "[REDACTED]"


# --- built-in pattern pack ---------------------------------------------------
# Tradeoff: false-positive rate vs miss rate. We err on more matches
# because the cost of leaking PII to logs is much higher than the cost
# of redacting a string that happened to look like one.
EMAIL = re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")
PHONE = re.compile(
    r"\b(?:\+?1[\s\-.]?)?(?:\(\d{3}\)|\d{3})[\s\-.]?\d{3}[\s\-.]?\d{4}\b"
)
SSN = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
CREDIT_CARD = re.compile(r"\b(?:\d[ -]*?){13,19}\b")
IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
IPV6 = re.compile(r"\b(?:[A-Fa-f0-9]{1,4}:){2,7}[A-Fa-f0-9]{1,4}\b")

# API key shapes for the providers we support (and their families).
OPENAI_KEY = re.compile(r"\bsk-[A-Za-z0-9_\-]{20,}\b")
ANTHROPIC_KEY = re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{20,}\b")
GENERIC_BEARER = re.compile(r"(?i)Bearer\s+[A-Za-z0-9._\-]{16,}")
AWS_ACCESS_KEY = re.compile(r"\b(AKIA|ASIA)[A-Z0-9]{16}\b")
GITHUB_TOKEN = re.compile(r"\bghp_[A-Za-z0-9]{36}\b")


_BUILTIN_PATTERNS: List[Pattern[str]] = [
    EMAIL,
    PHONE,
    SSN,
    CREDIT_CARD,
    IPV4,
    IPV6,
    OPENAI_KEY,
    ANTHROPIC_KEY,
    GENERIC_BEARER,
    AWS_ACCESS_KEY,
    GITHUB_TOKEN,
]


# --- redactor implementation -------------------------------------------------


@dataclass
class Redactor:
    """Callable that scrubs PII from arbitrary nested data.

    Built via :func:`builtin`; users can also pass a plain callable
    `(value) -> value` to wrap_*() and skip this class entirely.
    """

    patterns: List[Pattern[str]] = field(default_factory=list)
    token: str = REDACTION_TOKEN

    def __call__(self, value: Any) -> Any:
        return self._walk(value)

    def _walk(self, value: Any) -> Any:
        if isinstance(value, str):
            return self._redact_str(value)
        if isinstance(value, dict):
            return {k: self._walk(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self._walk(v) for v in value]
        if isinstance(value, tuple):
            return tuple(self._walk(v) for v in value)
        return value

    def _redact_str(self, text: str) -> str:
        out = text
        for pat in self.patterns:
            out = pat.sub(self.token, out)
        return out


def builtin(
    extra_patterns: Optional[Iterable[str | Pattern[str]]] = None,
    token: str = REDACTION_TOKEN,
) -> Redactor:
    """Construct a Redactor with the built-in pack plus optional extras.

    `extra_patterns` accepts raw regex strings or compiled patterns —
    raw strings are compiled with no flags (use inline `(?i)` for case
    insensitivity).

    Example:

        my_redactor = tool_pouch.redact.builtin(extra_patterns=[
            r"acct_\\d{6}",
            r"customer_token=[A-Za-z0-9]+",
        ])
        client = tool_pouch.wrap_openai(client, redact=my_redactor)
    """
    compiled: List[Pattern[str]] = list(_BUILTIN_PATTERNS)
    if extra_patterns:
        for pat in extra_patterns:
            compiled.append(pat if isinstance(pat, re.Pattern) else re.compile(pat))
    return Redactor(patterns=compiled, token=token)


def apply(
    redactor: Optional[Callable[[Any], Any]],
    value: Any,
) -> Any:
    """Run a redactor over a value, fail-open on errors.

    The wrap proxy uses this so a buggy user-supplied redactor cannot
    drop traces — at worst, an offending payload bypasses redaction.
    """
    if redactor is None:
        return value
    try:
        return redactor(value)
    except Exception:
        return value
