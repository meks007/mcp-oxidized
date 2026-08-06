"""
MCP server for Oxidized - network device configuration backup.
Transport: Streamable HTTP (default port 8000).
"""

import logging
import os
from typing import Optional

from fastmcp import FastMCP
from mcp_oxidized.diff_utils import blame_annotate, inline_diff, unified_diff
from mcp_oxidized.oxidized_client import OxidizedClient

logger = logging.getLogger(__name__)

mcp = FastMCP(
    name="mcp-oxidized",
    instructions=(
        "You have access to an Oxidized network configuration backup server. "
        "Use the available tools to list devices, look up devices by exact or "
        "partial name, inspect configurations, compare versions and analyse "
        "changes over time."
    ),
)

client = OxidizedClient()


class DeviceResolutionError(ValueError):
    """Raised when a device query has no unique Oxidized node match."""


def _node_group(node: dict) -> str:
    return str(node.get("group") or "default")


def _node_name(node: dict) -> str:
    return str(node.get("name") or node.get("full_name") or "")


def _node_full_name(node: dict) -> str:
    full_name = node.get("full_name")
    if full_name:
        return str(full_name)
    name = _node_name(node)
    group = _node_group(node)
    return f"{group}/{name}" if group and group != "default" else name


def _node_match_fields(node: dict) -> list[str]:
    """Return searchable fields for an Oxidized node."""
    fields = {
        _node_name(node),
        _node_full_name(node),
        str(node.get("ip") or ""),
    }
    return [field.casefold() for field in fields if field]


def _format_node(node: dict) -> str:
    return (
        f"name={_node_name(node)} "
        f"full_name={_node_full_name(node)} "
        f"group={_node_group(node)} "
        f"ip={node.get('ip', '')}"
    )


def _find_device_matches(query: str, group: Optional[str] = None) -> list[dict]:
    """Find exact or partial matches without calling an Oxidized node route."""
    query_normalized = query.strip().casefold()
    if not query_normalized:
        return []

    nodes = client.get_nodes()
    filtered = nodes
    if group:
        group_normalized = group.strip().casefold()
        filtered = [
            node
            for node in nodes
            if _node_group(node).casefold() == group_normalized
        ]

    exact = [
        node
        for node in filtered
        if query_normalized in _node_match_fields(node)
    ]
    if exact:
        return exact

    return [
        node
        for node in filtered
        if any(query_normalized in field for field in _node_match_fields(node))
    ]


def _resolve_device(query: str, group: Optional[str] = None) -> tuple[str, Optional[str], dict]:
    """Resolve an exact or partial device query to one canonical Oxidized node."""
    matches = _find_device_matches(query, group)

    if not matches:
        scope = f" in group '{group}'" if group else ""
        raise DeviceResolutionError(
            f"No Oxidized device matches '{query}'{scope}. "
            "Use find_devices to search by part of the device name."
        )

    if len(matches) > 1:
        candidates = "\n".join(f"- {_format_node(node)}" for node in matches[:25])
        more = f"\n... and {len(matches) - 25} more." if len(matches) > 25 else ""
        raise DeviceResolutionError(
            f"'{query}' matches {len(matches)} devices. Please provide a more "
            f"specific name or group:\n{candidates}{more}"
        )

    resolved = matches[0]
    resolved_name = _node_name(resolved)
    resolved_group = resolved.get("group") or (group or None)
    if not resolved_name:
        raise DeviceResolutionError(
            f"The match for '{query}' has no usable Oxidized device name."
        )
    return resolved_name, resolved_group, resolved


# ---------------------------------------------------------------------------
# Tool: list_devices
# ---------------------------------------------------------------------------

@mcp.tool()
def list_devices() -> str:
    """
    Return all devices managed by Oxidized.
    Each entry includes: name, model, group, ip, last backup time, status.
    """
    nodes = client.get_nodes()
    if not nodes:
        return "No devices found."
    lines = []
    for n in nodes:
        lines.append(
            f"name={n.get('name','')} model={n.get('model','')} "
            f"group={n.get('group','')} ip={n.get('ip','')} "
            f"last={n.get('last',{}).get('end','')} "
            f"status={n.get('last',{}).get('status','')}"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool: find_devices
# ---------------------------------------------------------------------------

@mcp.tool()
def find_devices(query: str, group: str = "") -> str:
    """
    Find Oxidized devices by exact or partial name, full name, or IP address.
    Matching is case-insensitive. Use this before other device tools when only
    part of a device name is known.

    Args:
        query: part of a device name, full device name, full group/name, or IP
        group: optional Oxidized device group filter
    """
    try:
        matches = _find_device_matches(query, group or None)
    except Exception as exc:
        logger.exception(
            "Oxidized device lookup failed for query=%s group=%s",
            query,
            group or None,
        )
        return f"Error looking up devices: {exc}"

    if not matches:
        scope = f" in group '{group}'" if group else ""
        return f"No devices match '{query}'{scope}."

    lines = [f"# Device matches for '{query}'"]
    lines.extend(f"- {_format_node(node)}" for node in matches)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool: get_device_status
# ---------------------------------------------------------------------------

@mcp.tool()
def get_device_status(node: str, group: str = "") -> str:
    """
    Return status details for a single device. The node may be an exact name
    or a unique partial name.

    Args:
        node: device hostname, IP, full name, or unique part of the name
        group: optional Oxidized device group
    """
    try:
        resolved_node, resolved_group, _ = _resolve_device(node, group or None)
        data = client.get_node(resolved_node)
    except Exception as exc:
        logger.exception(
            "Oxidized status lookup failed for node=%s group=%s",
            node,
            group or None,
        )
        return f"Error fetching status for '{node}': {exc}"

    last = data.get("last", {})
    return (
        f"resolved_device: {resolved_node}\n"
        f"resolved_group:  {resolved_group or ''}\n"
        f"name:   {data.get('name','')}\n"
        f"model:  {data.get('model','')}\n"
        f"group:  {data.get('group','')}\n"
        f"ip:     {data.get('ip','')}\n"
        f"status: {last.get('status','')}\n"
        f"start:  {last.get('start','')}\n"
        f"end:    {last.get('end','')}\n"
        f"job:    {last.get('job','')}\n"
    )


# ---------------------------------------------------------------------------
# Tool: get_device_versions
# ---------------------------------------------------------------------------

@mcp.tool()
def get_device_versions(node: str, group: str = "") -> str:
    """
    Return all available configuration versions for a device.
    The node may be an exact name or a unique partial name.
    Versions are numbered oldest-first: version 1 is the oldest and the
    highest number is the newest. Each entry includes its timestamp and OID.

    Args:
        node:  device hostname, full name, or unique part of the name
        group: device group in Oxidized (optional)
    """
    try:
        resolved_node, resolved_group, _ = _resolve_device(node, group or None)
        versions = client.get_versions(resolved_node, resolved_group)
    except Exception as exc:
        logger.exception(
            "Oxidized version list lookup failed for node=%s group=%s",
            node,
            group or None,
        )
        return f"Error fetching versions for '{node}': {exc}"

    if not versions:
        return f"No versions found for {resolved_node}."

    total = len(versions)
    lines = [
        f"# Versions for {resolved_node} (oldest to newest)",
        f"# Requested device: {node}",
    ]
    for index, version in enumerate(reversed(versions), start=1):
        timestamp = version.get("date") or version.get("time") or ""
        oid = version.get("oid") or version.get("id") or ""
        message = version.get("message") or version.get("msg") or ""
        line = f"version={index} time={timestamp} oid={oid}"
        if message:
            line += f" message={message}"
        lines.append(line)

    lines.append(f"total={total}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool: get_device_config_with_blame
# ---------------------------------------------------------------------------

@mcp.tool()
def get_device_config_with_blame(node: str, group: str = "") -> str:
    """
    Return the current configuration of a device with per-line blame annotation.
    The node may be an exact name or a unique partial name.

    Args:
        node:  device hostname, full name, or unique part of the name
        group: device group in Oxidized (optional)
    """
    try:
        resolved_node, resolved_group, _ = _resolve_device(node, group or None)
        config = client.fetch_config(resolved_node, resolved_group)
        versions = client.get_versions(resolved_node, resolved_group)
    except Exception as exc:
        logger.exception(
            "Oxidized blame prefetch failed for node=%s group=%s",
            node,
            group or None,
        )
        return f"Error fetching blame for '{node}': {exc}"

    enriched = []
    for ver in versions:
        oid = ver.get("oid") or ver.get("id", "")
        try:
            text = client.fetch_version(resolved_node, oid, resolved_group)
            ver["_config_lines"] = text.splitlines()
        except Exception:
            logger.exception(
                "Oxidized historical config fetch failed for node=%s group=%s oid=%s",
                resolved_node,
                resolved_group,
                oid,
            )
            ver["_config_lines"] = None
        enriched.append(ver)

    annotated = blame_annotate(config, enriched)
    return (
        f"# Config with blame annotation for {resolved_node}\n"
        f"# Requested device: {node}\n\n{annotated}"
    )


# ---------------------------------------------------------------------------
# Tool: get_config_with_inline_diff
# ---------------------------------------------------------------------------

@mcp.tool()
def get_config_with_inline_diff(
    node: str,
    ref_version: int,
    group: str = "",
    context_lines: int = 0,
) -> str:
    """
    Return the complete current configuration with inline change markers
    relative to a reference version number. The node may be an exact name or
    a unique partial name.

    Args:
        node:          device hostname, full name, or unique part of the name
        ref_version:   version number to compare against (1 = oldest, higher = newer)
        group:         device group in Oxidized (optional)
        context_lines: if > 0, only show this many unchanged lines around changes
    """
    try:
        resolved_node, resolved_group, _ = _resolve_device(node, group or None)
        current = client.fetch_config(resolved_node, resolved_group)
        versions = client.get_versions(resolved_node, resolved_group)
    except Exception as exc:
        logger.exception(
            "Oxidized inline diff prefetch failed for node=%s group=%s",
            node,
            group or None,
        )
        return f"Error fetching inline diff for '{node}': {exc}"

    idx = len(versions) - ref_version
    if idx < 0 or idx >= len(versions):
        return (
            f"Version {ref_version} does not exist for {resolved_node}. "
            f"Available versions: 1 to {len(versions)}."
        )

    ver = versions[idx]
    oid = ver.get("oid") or ver.get("id", "")
    try:
        ref_config = client.fetch_version(resolved_node, oid, resolved_group)
    except Exception as exc:
        logger.exception(
            "Oxidized reference config fetch failed for node=%s group=%s version=%s oid=%s",
            resolved_node,
            resolved_group,
            ref_version,
            oid,
        )
        return f"Error fetching version {ref_version} for '{resolved_node}': {exc}"

    result = inline_diff(ref_config, current, context_lines=context_lines)
    ref_date = (ver.get("date") or ver.get("time") or "")[:10]
    return (
        f"# Inline diff for {resolved_node}: current vs version {ref_version} ({ref_date})\n"
        f"# Requested device: {node}\n"
        f"# [+] added/changed  [-] removed  [ ] unchanged\n\n"
        f"{result}"
    )


# ---------------------------------------------------------------------------
# Tool: get_diff_between_versions
# ---------------------------------------------------------------------------

@mcp.tool()
def get_diff_between_versions(
    node: str,
    version_a: int,
    version_b: int,
    group: str = "",
    context_lines: int = 10,
) -> str:
    """
    Return a unified diff between two historical versions of a device
    configuration. The node may be an exact name or a unique partial name.

    Args:
        node:          device hostname, full name, or unique part of the name
        version_a:     first version number (older)
        version_b:     second version number (newer)
        group:         device group in Oxidized (optional)
        context_lines: number of unchanged lines to show around each change block
    """
    try:
        resolved_node, resolved_group, _ = _resolve_device(node, group or None)
        versions = client.get_versions(resolved_node, resolved_group)
    except Exception as exc:
        logger.exception(
            "Oxidized version list lookup failed for node=%s group=%s",
            node,
            group or None,
        )
        return f"Error fetching versions for '{node}': {exc}"

    total = len(versions)

    def fetch_ver(num: int):
        idx = total - num
        if idx < 0 or idx >= total:
            raise ValueError(
                f"Version {num} does not exist. Available: 1 to {total}."
            )
        ver = versions[idx]
        oid = ver.get("oid") or ver.get("id", "")
        text = client.fetch_version(resolved_node, oid, resolved_group)
        return text, ver.get("date") or ver.get("time") or ""

    try:
        text_a, date_a = fetch_ver(version_a)
        text_b, date_b = fetch_ver(version_b)
    except ValueError as exc:
        return str(exc)
    except Exception as exc:
        logger.exception(
            "Oxidized historical diff fetch failed for node=%s group=%s versions=%s,%s",
            resolved_node,
            resolved_group,
            version_a,
            version_b,
        )
        return f"Error fetching config for '{resolved_node}': {exc}"

    diff = unified_diff(
        text_a,
        text_b,
        old_label=f"v{version_a} ({date_a[:10]})",
        new_label=f"v{version_b} ({date_b[:10]})",
        context_lines=context_lines,
    )

    if not diff.strip():
        return (
            f"No differences for {resolved_node} between version {version_a} "
            f"({date_a[:10]}) and version {version_b} ({date_b[:10]})."
        )

    return (
        f"# Diff for {resolved_node}: v{version_a} ({date_a[:10]}) -> "
        f"v{version_b} ({date_b[:10]})\n"
        f"# Requested device: {node}\n"
        f"# Context lines: {context_lines}\n\n"
        f"{diff}"
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO").upper())
    port = int(os.environ.get("MCP_PORT", "8000"))
    mcp.run(transport="http", host="0.0.0.0", port=port)
