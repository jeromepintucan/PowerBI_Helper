"""
Helpers for reading/writing Power BI's "property value" JSON encoding.

Power BI report JSON (both the classic single-file report.json layout and the
newer PBIR exploded format) encodes most boolean/format-pane properties as
either:

    "show": false

or the older / more common "expression" wrapper:

    "show": {"expr": {"Literal": {"Value": "false"}}}

Both styles have been observed across schema versions, and a single project
can be internally consistent (all properties in one visual tend to use the
same style). Rather than assuming one style, these helpers can READ either
style, and when WRITING prefer to mirror whatever style is already present
on the property being changed (or elsewhere in the same object) so the
resulting file stays stylistically consistent with what Power BI Desktop
itself would have written.
"""
from __future__ import annotations
from typing import Any, Optional


def read_bool(value: Any) -> Optional[bool]:
    """Best-effort extraction of a boolean from either encoding style."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        if value.lower() == "true":
            return True
        if value.lower() == "false":
            return False
    if isinstance(value, dict):
        try:
            lit = value["expr"]["Literal"]["Value"]
            return read_bool(lit)
        except (KeyError, TypeError):
            return None
    return None


def _looks_wrapped(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and isinstance(value.get("expr"), dict)
        and isinstance(value["expr"].get("Literal"), dict)
        and "Value" in value["expr"]["Literal"]
    )


def detect_style(container: dict) -> str:
    """
    Look at sibling properties inside a `properties` dict (or similar) to
    guess which encoding style this file/object uses.
    Returns "wrapped" or "plain". Defaults to "wrapped" (the more
    conservative / widely-compatible choice) when no signal is found.
    """
    if isinstance(container, dict):
        for v in container.values():
            if _looks_wrapped(v):
                return "wrapped"
            if isinstance(v, bool):
                return "plain"
    return "wrapped"


def make_bool(value: bool, style: str) -> Any:
    if style == "plain":
        return value
    return {"expr": {"Literal": {"Value": "true" if value else "false"}}}


def set_bool(container: dict, key: str, value: bool, style: Optional[str] = None) -> None:
    """
    Set container[key] to a boolean-valued property, matching the existing
    style of that key if it's already present, otherwise the style detected
    from sibling keys in `container`, otherwise the given/default style.
    """
    if key in container:
        existing_style = "wrapped" if _looks_wrapped(container[key]) else (
            "plain" if isinstance(container[key], bool) else None
        )
    else:
        existing_style = None

    final_style = existing_style or style or detect_style(container)
    container[key] = make_bool(value, final_style)
