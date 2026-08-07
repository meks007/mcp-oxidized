"""
MCP server for Oxidized - network device configuration backup.
Transport: Streamable HTTP (default port 8000).
"""

import logging
import os
from typing import Optional

from fastmcp import Context, FastMCP
from fastmcp.resources import ResourceContent, ResourceResult

from mcp_oxidized.diff_utils import blame_annotate, inline_diff, unified_diff
from mcp_oxidized.oxidized_client import OxidizedClient
from mcp_oxidized.prepared_config_store import get_prepared, set_prepared

logger = logging.getLogger(__name__)

mcp = FastMCP(
    name="mcp-oxidized",
    instructions=(
        "You have access to an Oxidized network configuration backup server. "
        "Use the available tools to list devices, look up devices by exact or "
        "partial name, inspect configurations, compare versions and analyse "
        "changes over time. Use prepare_config before reading the current "
        "configuration resource and prepare_blame before reading the blame resource."
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
    if resolved_group and str(resolved_group).casefold() == "default":
        resolved_group = None
    if not resolved_name:
        raise DeviceResolutionError(
            f"The match for '{query}' has no usable Oxidized device name."
        )
    return resolved_name, resolved_group, resolved


def _session_id(ctx: Context) -> str:
    """Return the current HTTP MCP session id required for prepared resources."""
    session_id = getattr(ctx, "session_id", None)
    if callable(session_id):
        session_id = session_id()
    if not session_id:
        raise RuntimeError("No MCP session id is available for this request.")
    return str(session_id)


def _text_content(content: str) -> ResourceContent:
    """Build resource content without repeating the static resource URI."""
    return ResourceContent(
        content=content,
        mime_type="text/plain; charset=utf-8",
    )


def _prepared_resource_error(kind: str, prepare_tool: str, resource_uri: str) -> ResourceResult:
    """Return a readable resource response when no matching prepare call exists."""
    return ResourceResult(
        contents=[
            _text_content(
                f"No {kind} has been prepared for this session. "
                f"Call {prepare_tool} first, then read {resource_uri}."
            )
        ]
    )


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
# Tools: prepare_config and prepare_blame
# ---------------------------------------------------------------------------


def _prepare_device(
    node: str,
    group: str,
    slot: str,
    label: str,
    resource_uri: str,
    ctx: Context,
) -> str:
    """Resolve and record a device selection for one static resource."""
    try:
        resolved_node, resolved_group, data = _resolve_device(node, group or None)
        session_id = _session_id(ctx)
        set_prepared(session_id, slot, resolved_node, resolved_group)
    except Exception as exc:
        logger.exception(
            "Oxidized %s preparation failed for node=%s group=%s",
            label,
            node,
            group or None,
        )
        return f"Error preparing {label} for '{node}': {exc}"

    last = data.get("last") or {}
    return (
        f"{label.capitalize()} prepared.\n"
        f"requested_device: {node}\n"
        f"resolved_device: {resolved_node}\n"
        f"group: {resolved_group or 'default'}\n"
        f"model: {data.get('model', '')}\n"
        f"ip: {data.get('ip', '')}\n"
        f"last_backup_status: {last.get('status', '')}\n\n"
        f"Read {resource_uri} to retrieve the prepared {label}."
    )


@mcp.tool()
def prepare_config(node: str, group: str = "", ctx: Context = None) -> str:
    """
    Prepare one device's current configuration for the static get_content resource.
    The node may be an exact name or a unique partial name. This tool returns only
    device metadata; read oxidized://config/get_content for the configuration.

    Args:
        node: device hostname, IP, full name, or unique part of the name
        group: optional Oxidized device group
    """
    if ctx is None:
        return "Error preparing configuration: MCP request context is unavailable."
    return _prepare_device(
        node,
        group,
        "content",
        "configuration",
        "oxidized://config/get_content",
        ctx,
    )


@mcp.tool()
def prepare_blame(node: str, group: str = "", ctx: Context = None) -> str:
    """
    Prepare one device's configuration blame for the static get_blame resource.
    The node may be an exact name or a unique partial name. This tool returns only
    device metadata; read oxidized://config/get_blame for the annotated config.

    Args:
        node: device hostname, IP, full name, or unique part of the name
        group: optional Oxidized device group
    """
    if ctx is None:
        return "Error preparing blame: MCP request context is unavailable."
    return _prepare_device(
        node,
        group,
        "blame",
        "blame",
        "oxidized://config/get_blame",
        ctx,
    )


# ---------------------------------------------------------------------------
# Resources: get_content and get_blame
# ---------------------------------------------------------------------------

@mcp.resource(
    "oxidized://config/get_content",
    name="Get prepared current configuration",
    description=(
        "Returns the current configuration selected by prepare_config in this MCP "
        "session. Call prepare_config first. The configuration is fetched live from "
        "Oxidized for every resource read."
    ),
    mime_type="text/plain; charset=utf-8",
)
def get_content(ctx: Context) -> ResourceResult:
    """Fetch the prepared current configuration without storing configuration data."""
    resource_uri = "oxidized://config/get_content"
    try:
        prepared = get_prepared(_session_id(ctx), "content")
    except Exception as exc:
        logger.exception("Oxidized content resource could not obtain session id")
        return ResourceResult(
            contents=[_text_content(f"Could not read the prepared configuration: {exc}")]
        )

    if prepared is None:
        return _prepared_resource_error("configuration", "prepare_config", resource_uri)

    try:
        content = client.fetch_config(prepared.node, prepared.group)
    except Exception as exc:
        logger.exception(
            "Oxidized configuration download failed for node=%s group=%s",
            prepared.node,
            prepared.group,
        )
        content = (
            f"Could not download the current configuration for {prepared.node} "
            f"in group {prepared.group or 'default'}: {exc}"
        )

    return ResourceResult(contents=[_text_content(content)])


@mcp.resource(
    "oxidized://config/get_blame",
    name="Get prepared configuration blame",
    description=(
        "Returns the annotated configuration selected by prepare_blame in this MCP "
        "session. Call prepare_blame first. The current and historical configurations "
        "are fetched live from Oxidized for every resource read."
    ),
    mime_type="text/plain; charset=utf-8",
)
def get_blame(ctx: Context) -> ResourceResult:
    """Build the prepared configuration blame without storing configuration data."""
    resource_uri = "oxidized://config/get_blame"
    try:
        prepared = get_prepared(_session_id(ctx), "blame")
    except Exception as exc:
        logger.exception("Oxidized blame resource could not obtain session id")
        return ResourceResult(contents=[_text_content(f"Could not read the prepared blame: {exc}")])

    if prepared is None:
        return _prepared_resource_error("blame", "prepare_blame", resource_uri)

    try:
        config = client.fetch_config(prepared.node, prepared.group)
        versions = client.get_versions(prepared.node, prepared.group)
        enriched = []
        for version in versions:
            oid = version.get("oid") or version.get("id", "")
            try:
                text = client.fetch_version(prepared.node, oid, prepared.group)
                version["_config_lines"] = text.splitlines()
            except Exception:
                logger.exception(
                    "Oxidized historical config fetch failed for node=%s group=%s oid=%s",
                    prepared.node,
                    prepared.group,
                    oid,
                )
                version["_config_lines"] = None
            enriched.append(version)

        content = blame_annotate(config, enriched)
    except Exception as exc:
        logger.exception(
            "Oxidized blame generation failed for node=%s group=%s",
            prepared.node,
            prepared.group,
        )
        content = (
            f"Could not generate configuration blame for {prepared.node} "
            f"in group {prepared.group or 'default'}: {exc}"
        )

    return ResourceResult(contents=[_text_content(content)])


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
