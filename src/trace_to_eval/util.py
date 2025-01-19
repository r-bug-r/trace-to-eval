"""Small deterministic helpers shared across the package."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import unicodedata
from typing import Any

_ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
# Keep newline and tab; strip the rest of C0/C1 plus bidi controls.
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f\u202a-\u202e\u2066-\u2069]")
_INDEX_RE = re.compile(r"^([A-Za-z0-9_-]+)(?:\[(\d+)\])?$")


def utc_now() -> str:
    """Return an ISO-8601 UTC timestamp with a ``Z`` suffix."""
    return dt.datetime.now(dt.UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def canonical_json(value: Any) -> str:
    """Serialize a JSON value deterministically for hashing."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def stable_hash(value: Any, length: int = 24) -> str:
    """Return a truncated SHA-256 hex digest of a canonical JSON value."""
    if length < 8 or length > 64:
        raise ValueError("hash length must be between 8 and 64")
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()[:length]


def normalize_string(value: str) -> str:
    """Normalize a captured string without changing its internal layout."""
    text = unicodedata.normalize("NFKC", value)
    return text.replace("\r\n", "\n").replace("\r", "\n").strip()


def normalize_text(value: str) -> str:
    """Normalize text for property comparison.

    Collapses horizontal whitespace, trims line ends, and reduces runs of blank lines.
    Line structure is preserved so multi-line outputs are still distinguishable.
    """
    text = normalize_string(value)
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.split("\n")]
    text = "\n".join(lines).strip()
    return re.sub(r"\n{3,}", "\n\n", text)


def safe_text(value: str, *, max_length: int = 400) -> str:
    """Sanitize untrusted text before terminal or report rendering.

    Removes ANSI escapes, C0/C1 controls, and bidi overrides; truncates with an ellipsis.
    """
    text = _ANSI_RE.sub("", value)
    text = _CONTROL_RE.sub("", text).replace("\r\n", "\n").replace("\r", "\n")
    if len(text) > max_length:
        text = text[: max_length - 1] + "\u2026"
    return text


def truncate(value: Any, *, max_length: int = 400) -> str:
    """Render an arbitrary JSON value as sanitized, bounded text."""
    if isinstance(value, str):
        return safe_text(value, max_length=max_length)
    try:
        rendered = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    except (TypeError, ValueError):
        rendered = repr(value)
    return safe_text(rendered, max_length=max_length)


def deep_get(value: Any, path: str) -> Any:
    """Resolve a dotted path such as ``output.structured.items[0].name``.

    Raises ``KeyError`` or ``IndexError`` when the path does not exist.
    """
    current = value
    for token in path.split("."):
        if not token:
            raise KeyError(path)
        match = _INDEX_RE.match(token)
        if match is None:
            raise KeyError(path)
        name, index = match.group(1), match.group(2)
        if not isinstance(current, dict) or name not in current:
            raise KeyError(path)
        current = current[name]
        if index is not None:
            if not isinstance(current, list):
                raise KeyError(path)
            current = current[int(index)]
    return current


def percentile(values: list[float], fraction: float) -> float:
    """Nearest-rank percentile; returns 0.0 for an empty list."""
    if not values:
        return 0.0
    if not 0.0 < fraction <= 1.0:
        raise ValueError("fraction must be in (0, 1]")
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(-(-fraction * len(ordered) // 1)) - 1))
    return ordered[index]
