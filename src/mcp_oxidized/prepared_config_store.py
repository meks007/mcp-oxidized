"""In-memory prepared selections keyed by a hashed Bearer token."""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from threading import RLock
from typing import Literal

from fastmcp.server.dependencies import get_http_headers


logger = logging.getLogger(__name__)

PreparedKind = Literal["content", "blame"]


@dataclass(frozen=True, slots=True)
class PreparedConfig:
    """Canonical Oxidized node coordinates selected by a prepare tool."""

    node: str
    group: str | None


_lock = RLock()
_prepared: dict[str, dict[PreparedKind, PreparedConfig]] = {}


def _token_key() -> str:
    """Return a non-reversible store key for the current Bearer token."""
    headers = get_http_headers() or {}
    authorization = headers.get("authorization", "").strip()
    scheme, separator, token = authorization.partition(" ")

    logger.info(
        "Prepared selection auth debug: authorization_present=%s scheme=%r "
        "token_present=%s token_length=%d header_names=%s",
        bool(authorization),
        scheme,
        bool(token.strip()),
        len(token.strip()),
        ",".join(sorted(str(name) for name in headers.keys())),
    )

    if scheme.casefold() != "bearer" or not separator or not token.strip():
        raise RuntimeError(
            "Missing Bearer token. Send Authorization: Bearer <token> with every request."
        )
    return hashlib.sha256(token.strip().encode("utf-8")).hexdigest()


def set_prepared(
    _session_id: str,
    kind: PreparedKind,
    node: str,
    group: str | None,
) -> None:
    """Store only a selected node reference, never configuration content."""
    token_key = _token_key()
    with _lock:
        slots = _prepared.setdefault(token_key, {})
        slots[kind] = PreparedConfig(node=node, group=group)


def get_prepared(_session_id: str, kind: PreparedKind) -> PreparedConfig | None:
    """Return the selected node reference for the current Bearer token and kind."""
    token_key = _token_key()
    with _lock:
        return _prepared.get(token_key, {}).get(kind)
