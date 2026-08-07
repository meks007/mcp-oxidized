"""In-memory prepared selections keyed by a hashed Bearer token."""

from __future__ import annotations

from dataclasses import dataclass
from threading import RLock
from typing import Literal


PreparedKind = Literal["content", "blame"]


@dataclass(frozen=True, slots=True)
class PreparedConfig:
    """Canonical Oxidized node coordinates selected by a prepare tool."""

    node: str
    group: str | None


_lock = RLock()
_prepared: dict[str, dict[PreparedKind, PreparedConfig]] = {}


def set_prepared(
    token_key: str,
    kind: PreparedKind,
    node: str,
    group: str | None,
) -> None:
    """Store only a selected node reference, never configuration content."""
    with _lock:
        slots = _prepared.setdefault(token_key, {})
        slots[kind] = PreparedConfig(node=node, group=group)


def get_prepared(token_key: str, kind: PreparedKind) -> PreparedConfig | None:
    """Return the selected node reference for this token and resource type."""
    with _lock:
        return _prepared.get(token_key, {}).get(kind)
