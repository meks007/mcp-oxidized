"""In-memory prepared selections keyed by a hashed Bearer token."""

from __future__ import annotations

import hashlib
import logging
from contextvars import ContextVar
from dataclasses import dataclass
from threading import RLock
from typing import Literal

from fastmcp.server.dependencies import get_http_headers


logger = logging.getLogger(__name__)

PreparedKind = Literal["contents", "blame"]


_request_authorization: ContextVar[str] = ContextVar(
    "request_authorization",
    default="",
)


@dataclass(frozen=True, slots=True)
class PreparedConfig:
    """Canonical Oxidized node coordinates selected by a prepare tool."""

    node: str
    group: str | None


_lock = RLock()
_prepared: dict[str, dict[PreparedKind, list[PreparedConfig]]] = {}


def set_request_authorization(value: str):
    """Set the current request Authorization header from ASGI middleware."""
    return _request_authorization.set(value)


def reset_request_authorization(token) -> None:
    """Restore the previous request Authorization header value."""
    _request_authorization.reset(token)


def _token_key() -> str:
    """Return a non-reversible store key for the current Bearer token."""
    request_authorization = _request_authorization.get().strip()
    dependency_headers = get_http_headers() or {}
    dependency_authorization = dependency_headers.get("authorization", "").strip()
    authorization = request_authorization or dependency_authorization
    scheme, separator, token = authorization.partition(" ")

    logger.info(
        "Prepared selection auth debug: middleware_authorization=%r "
        "dependency_authorization=%r selected_authorization=%r",
        request_authorization,
        dependency_authorization,
        authorization,
    )

    if scheme.casefold() != "bearer" or not separator or not token.strip():
        raise RuntimeError(
            "Missing Bearer token. Send Authorization: Bearer <token> with every request."
        )
    return hashlib.sha256(token.strip().encode("utf-8")).hexdigest()


def set_prepared(
    _session_id: str,
    kind: PreparedKind,
    selections: list[PreparedConfig],
) -> None:
    """Store selected node references without configuration content."""
    token_key = _token_key()
    unique: list[PreparedConfig] = []
    seen: set[tuple[str, str | None]] = set()
    for selection in selections:
        key = (selection.node, selection.group)
        if key not in seen:
            seen.add(key)
            unique.append(selection)

    with _lock:
        slots = _prepared.setdefault(token_key, {})
        slots[kind] = unique


def get_prepared(
    _session_id: str,
    kind: PreparedKind,
) -> list[PreparedConfig]:
    """Return selected node references for the current Bearer token and kind."""
    token_key = _token_key()
    with _lock:
        return list(_prepared.get(token_key, {}).get(kind, []))
