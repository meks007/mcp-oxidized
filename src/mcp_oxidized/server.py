"""MCP server for Oxidized network configuration backups."""

import logging
import os
from typing import Optional
from urllib.parse import quote

from fastmcp import Context, FastMCP
from fastmcp.resources import ResourceContent, ResourceResult

from mcp_oxidized.diff_utils import blame_annotate, inline_diff, unified_diff
from mcp_oxidized.oxidized_client import OxidizedClient
from mcp_oxidized.prepared_config_store import PreparedConfig, get_prepared, set_prepared

logger = logging.getLogger(__name__)

mcp = FastMCP(
    name="mcp-oxidized",
    instructions=(
        "You have access to an Oxidized network configuration backup server. "
        "Use tools to list devices, inspect configurations, compare versions, and "
        "analyze changes. Use prepare_configs before reading current configurations "
        "and prepare_blame before reading configuration blame."
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
    fields = {_node_name(node), _node_full_name(node), str(node.get("ip") or "")}
    return [field.casefold() for field in fields if field]


def _format_node(node: dict) -> str:
    return (
        f"name={_node_name(node)} full_name={_node_full_name(node)} "
        f"group={_node_group(node)} ip={node.get('ip', '')}"
    )


def _find_device_matches(query: str, group: Optional[str] = None) -> list[dict]:
    query_normalized = query.strip().casefold()
    if not query_normalized:
        return []
    nodes = client.get_nodes()
    if group:
        group_normalized = group.strip().casefold()
        nodes = [node for node in nodes if _node_group(node).casefold() == group_normalized]
    exact = [node for node in nodes if query_normalized in _node_match_fields(node)]
    if exact:
        return exact
    return [
        node for node in nodes
        if any(query_normalized in field for field in _node_match_fields(node))
    ]


def _resolve_device(query: str, group: Optional[str] = None) -> tuple[str, Optional[str], dict]:
    matches = _find_device_matches(query, group)
    if not matches:
        scope = f" in group '{group}'" if group else ""
        raise DeviceResolutionError(
            f"No Oxidized device matches '{query}'{scope}. Use find_devices to search."
        )
    if len(matches) > 1:
        candidates = "\n".join(f"- {_format_node(node)}" for node in matches[:25])
        more = f"\n... and {len(matches) - 25} more." if len(matches) > 25 else ""
        raise DeviceResolutionError(
            f"'{query}' matches {len(matches)} devices. Provide a more specific "
            f"name or group:\n{candidates}{more}"
        )
    resolved = matches[0]
    name = _node_name(resolved)
    resolved_group = resolved.get("group") or (group or None)
    if resolved_group and str(resolved_group).casefold() == "default":
        resolved_group = None
    if not name:
        raise DeviceResolutionError(f"The match for '{query}' has no usable device name.")
    return name, resolved_group, resolved


def _session_id(ctx: Context) -> str:
    session_id = getattr(ctx, "session_id", None)
    if callable(session_id):
        session_id = session_id()
    if not session_id:
        raise RuntimeError("No MCP session id is available for this request.")
    return str(session_id)


def _text_content(content: str) -> ResourceContent:
    return ResourceContent(content=content, mime_type="text/plain; charset=utf-8")


def _resource_uri(selection: PreparedConfig) -> str:
    group = selection.group or "default"
    return f"oxidized://config/{quote(group, safe='')}/{quote(selection.node, safe='')}"


@mcp.tool()
def list_devices() -> str:
    """Return all devices managed by Oxidized."""
    nodes = client.get_nodes()
    if not nodes:
        return "No devices found."
    return "\n".join(
        f"name={node.get('name', '')} model={node.get('model', '')} "
        f"group={node.get('group', '')} ip={node.get('ip', '')} "
        f"last={node.get('last', {}).get('end', '')} "
        f"status={node.get('last', {}).get('status', '')}"
        for node in nodes
    )


@mcp.tool()
def find_devices(query: str, group: str = "") -> str:
    """Find devices by exact or partial name, full name, or IP address."""
    try:
        matches = _find_device_matches(query, group or None)
    except Exception as exc:
        logger.exception("Oxidized device lookup failed")
        return f"Error looking up devices: {exc}"
    if not matches:
        scope = f" in group '{group}'" if group else ""
        return f"No devices match '{query}'{scope}."
    return "\n".join([f"# Device matches for '{query}'"] + [
        f"- {_format_node(node)}" for node in matches
    ])


@mcp.tool()
def get_device_status(node: str, group: str = "") -> str:
    """Return backup status details for one device."""
    try:
        resolved_node, resolved_group, _ = _resolve_device(node, group or None)
        data = client.get_node(resolved_node)
    except Exception as exc:
        logger.exception("Oxidized status lookup failed")
        return f"Error fetching status for '{node}': {exc}"
    last = data.get("last", {})
    return (
        f"resolved_device: {resolved_node}\nresolved_group:  {resolved_group or ''}\n"
        f"name:   {data.get('name', '')}\nmodel:  {data.get('model', '')}\n"
        f"group:  {data.get('group', '')}\nip:     {data.get('ip', '')}\n"
        f"status: {last.get('status', '')}\nstart:  {last.get('start', '')}\n"
        f"end:    {last.get('end', '')}\njob:    {last.get('job', '')}\n"
    )


@mcp.tool()
def get_device_versions(node: str, group: str = "") -> str:
    """Return configuration versions for one device, oldest first."""
    try:
        resolved_node, _, _ = _resolve_device(node, group or None)
        versions = client.get_versions(resolved_node, group or None)
    except Exception as exc:
        logger.exception("Oxidized version list lookup failed")
        return f"Error fetching versions for '{node}': {exc}"
    if not versions:
        return f"No versions found for {resolved_node}."
    lines = [f"# Versions for {resolved_node} (oldest to newest)", f"# Requested device: {node}"]
    for index, version in enumerate(reversed(versions), start=1):
        timestamp = version.get("date") or version.get("time") or ""
        oid = version.get("oid") or version.get("id") or ""
        message = version.get("message") or version.get("msg") or ""
        line = f"version={index} time={timestamp} oid={oid}"
        if message:
            line += f" message={message}"
        lines.append(line)
    lines.append(f"total={len(versions)}")
    return "\n".join(lines)


@mcp.tool()
def prepare_configs(nodes: list[str], group: str = "", ctx: Context = None) -> str:
    """Prepare one or more devices for oxidized://config/get_contents.

    Each device may be an exact name or a unique partial name. The tool returns
    metadata only; the resource retrieves current configurations live.
    """
    if ctx is None:
        return "Error preparing configurations: MCP request context is unavailable."
    if not nodes:
        return "Error preparing configurations: provide at least one device."
    selections: list[PreparedConfig] = []
    lines = []
    for requested_node in nodes:
        try:
            resolved_node, resolved_group, data = _resolve_device(requested_node, group or None)
            selections.append(PreparedConfig(node=resolved_node, group=resolved_group))
            last = data.get("last") or {}
            lines.append(
                f"prepared requested_device={requested_node} resolved_device={resolved_node} "
                f"group={resolved_group or 'default'} model={data.get('model', '')} "
                f"ip={data.get('ip', '')} last_backup_status={last.get('status', '')}"
            )
        except Exception as exc:
            logger.exception("Oxidized configuration preparation failed for node=%s", requested_node)
            lines.append(f"failed requested_device={requested_node} error={exc}")
    if not selections:
        return "No configurations were prepared.\n" + "\n".join(lines)
    try:
        set_prepared(_session_id(ctx), "contents", selections)
    except Exception as exc:
        logger.exception("Oxidized configuration selection storage failed")
        return f"Error storing prepared configurations: {exc}"
    return "\n".join(lines + [
        "Read oxidized://config/get_contents to retrieve all prepared configurations."
    ])


@mcp.resource(
    "oxidized://config/get_contents",
    name="Get prepared current configurations",
    description=(
        "Returns current configurations selected by prepare_configs in this MCP "
        "session. Each configuration is fetched live from Oxidized."
    ),
    mime_type="text/plain; charset=utf-8",
)
def get_contents(ctx: Context) -> list[dict]:
    """Fetch all prepared configurations as separate resource content entries."""
    try:
        selections = get_prepared(_session_id(ctx), "contents")
    except Exception as exc:
        logger.exception("Oxidized contents resource could not obtain prepared selections")
        return [{
            "uri": "oxidized://config/error",
            "text": f"Could not read prepared configurations: {exc}",
            "mimeType": "text/plain; charset=utf-8",
        }]
    if not selections:
        return [{
            "uri": "oxidized://config/error",
            "text": "No configurations have been prepared for this session. Call prepare_configs first.",
            "mimeType": "text/plain; charset=utf-8",
        }]
    contents = []
    for selection in selections:
        try:
            text = client.fetch_config(selection.node, selection.group)
        except Exception as exc:
            logger.exception("Oxidized configuration download failed for node=%s", selection.node)
            text = (
                f"Could not download the current configuration for {selection.node} "
                f"in group {selection.group or 'default'}: {exc}"
            )
        contents.append({
            "uri": _resource_uri(selection),
            "text": text,
            "mimeType": "text/plain; charset=utf-8",
        })
    return contents


@mcp.tool()
def prepare_blame(node: str, group: str = "", ctx: Context = None) -> str:
    """Prepare one device's configuration blame for oxidized://config/get_blame."""
    if ctx is None:
        return "Error preparing blame: MCP request context is unavailable."
    try:
        resolved_node, resolved_group, data = _resolve_device(node, group or None)
        set_prepared(
            _session_id(ctx),
            "blame",
            [PreparedConfig(node=resolved_node, group=resolved_group)],
        )
    except Exception as exc:
        logger.exception("Oxidized blame preparation failed")
        return f"Error preparing blame for '{node}': {exc}"
    last = data.get("last") or {}
    return (
        f"Blame prepared.\nrequested_device: {node}\nresolved_device: {resolved_node}\n"
        f"group: {resolved_group or 'default'}\nmodel: {data.get('model', '')}\n"
        f"ip: {data.get('ip', '')}\nlast_backup_status: {last.get('status', '')}\n\n"
        "Read oxidized://config/get_blame to retrieve the prepared blame."
    )


@mcp.resource(
    "oxidized://config/get_blame",
    name="Get prepared configuration blame",
    description="Returns configuration blame selected by prepare_blame in this MCP session.",
    mime_type="text/plain; charset=utf-8",
)
def get_blame(ctx: Context) -> ResourceResult:
    """Build prepared configuration blame without storing configuration data."""
    try:
        selections = get_prepared(_session_id(ctx), "blame")
    except Exception as exc:
        logger.exception("Oxidized blame resource could not obtain session id")
        return ResourceResult(contents=[_text_content(f"Could not read prepared blame: {exc}")])
    if not selections:
        return ResourceResult(contents=[_text_content(
            "No blame has been prepared for this session. Call prepare_blame first."
        )])
    prepared = selections[0]
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
                logger.exception("Oxidized historical config fetch failed for oid=%s", oid)
                version["_config_lines"] = None
            enriched.append(version)
        content = blame_annotate(config, enriched)
    except Exception as exc:
        logger.exception("Oxidized blame generation failed")
        content = f"Could not generate configuration blame for {prepared.node}: {exc}"
    return ResourceResult(contents=[_text_content(content)])


@mcp.tool()
def get_config_with_inline_diff(
    node: str, ref_version: int, group: str = "", context_lines: int = 0
) -> str:
    """Return the current configuration with inline markers against one version."""
    try:
        resolved_node, resolved_group, _ = _resolve_device(node, group or None)
        current = client.fetch_config(resolved_node, resolved_group)
        versions = client.get_versions(resolved_node, resolved_group)
        index = len(versions) - ref_version
        if index < 0 or index >= len(versions):
            return f"Version {ref_version} does not exist for {resolved_node}. Available versions: 1 to {len(versions)}."
        version = versions[index]
        reference = client.fetch_version(
            resolved_node, version.get("oid") or version.get("id", ""), resolved_group
        )
    except Exception as exc:
        logger.exception("Oxidized inline diff failed")
        return f"Error fetching inline diff for '{node}': {exc}"
    date = (version.get("date") or version.get("time") or "")[:10]
    return (
        f"# Inline diff for {resolved_node}: current vs version {ref_version} ({date})\n"
        f"# Requested device: {node}\n# [+] added/changed  [-] removed  [ ] unchanged\n\n"
        f"{inline_diff(reference, current, context_lines=context_lines)}"
    )


@mcp.tool()
def get_diff_between_versions(
    node: str, version_a: int, version_b: int, group: str = "", context_lines: int = 10
) -> str:
    """Return a unified diff between two historical configuration versions."""
    try:
        resolved_node, resolved_group, _ = _resolve_device(node, group or None)
        versions = client.get_versions(resolved_node, resolved_group)
        total = len(versions)
        def fetch_version(number: int):
            index = total - number
            if index < 0 or index >= total:
                raise ValueError(f"Version {number} does not exist. Available: 1 to {total}.")
            version = versions[index]
            text = client.fetch_version(
                resolved_node, version.get("oid") or version.get("id", ""), resolved_group
            )
            return text, version.get("date") or version.get("time") or ""
        text_a, date_a = fetch_version(version_a)
        text_b, date_b = fetch_version(version_b)
    except Exception as exc:
        logger.exception("Oxidized historical diff fetch failed")
        return f"Error fetching diff for '{node}': {exc}"
    diff = unified_diff(
        text_a, text_b, f"v{version_a} ({date_a[:10]})", f"v{version_b} ({date_b[:10]})", context_lines
    )
    if not diff.strip():
        return f"No differences for {resolved_node} between version {version_a} and version {version_b}."
    return (
        f"# Diff for {resolved_node}: v{version_a} ({date_a[:10]}) -> v{version_b} ({date_b[:10]})\n"
        f"# Requested device: {node}\n# Context lines: {context_lines}\n\n{diff}"
    )


if __name__ == "__main__":
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO").upper())
    port = int(os.environ.get("MCP_PORT", "8000"))
    mcp.run(transport="http", host="0.0.0.0", port=port)
