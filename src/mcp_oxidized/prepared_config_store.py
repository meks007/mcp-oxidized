"""In-memory, session-scoped selections for static config resources."""

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
    session_id: str,
    kind: PreparedKind,
    node: str,
    group: str | None,
) -> None:
    """Store only a selected node reference, never configuration content."""
    with _lock:
        slots = _prepared.setdefault(session_id, {})
        slots[kind] = PreparedConfig(node=node, group=group)


def get_prepared(session_id: str, kind: PreparedKind) -> PreparedConfig | None:
    """Return the selected node reference for this session and resource type."""
    with _lock:
        return _prepared.get(session_id, {}).get(kind)
