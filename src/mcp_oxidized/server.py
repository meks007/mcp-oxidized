"""
MCP server for Oxidized - network device configuration backup.
Transport: Streamable HTTP (default port 8000).
"""

import logging
import os

from fastmcp import FastMCP
from mcp_oxidized.diff_utils import blame_annotate, inline_diff, unified_diff
from mcp_oxidized.oxidized_client import OxidizedClient

logger = logging.getLogger(__name__)

mcp = FastMCP(
    name="mcp-oxidized",
    instructions=(
        "You have access to an Oxidized network configuration backup server. "
        "Use the available tools to list devices, inspect configurations, "
        "compare versions and analyse changes over time."
    ),
)

client = OxidizedClient()


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
# Tool: get_device_status
# ---------------------------------------------------------------------------

@mcp.tool()
def get_device_status(node: str) -> str:
    """
    Return status details for a single device.
    Includes: last backup time, success/failure status, error messages.

    Args:
        node: device hostname or IP as known to Oxidized
    """
    try:
        data = client.get_node(node)
    except Exception as exc:
        logger.exception("Oxidized status lookup failed for node=%s", node)
        return f"Error fetching status for {node}: {exc}"

    last = data.get("last", {})
    return (
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
    Versions are numbered oldest-first: version 1 is the oldest and the
    highest number is the newest. Each entry includes its timestamp and OID.

    Args:
        node:  device hostname or IP
        group: device group in Oxidized (optional)
    """
    try:
        versions = client.get_versions(node, group or None)
    except Exception as exc:
        logger.exception(
            "Oxidized version list lookup failed for node=%s group=%s",
            node,
            group or None,
        )
        return f"Error fetching versions for {node}: {exc}"

    if not versions:
        return f"No versions found for {node}."

    total = len(versions)
    lines = [f"# Versions for {node} (oldest to newest)"]
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
    Each line is prefixed with the version number and date that last introduced it.
    Useful for questions like: 'show me the current config and who changed each line'.

    Args:
        node:  device hostname or IP
        group: device group in Oxidized (optional)
    """
    try:
        config = client.fetch_config(node, group or None)
        versions = client.get_versions(node, group or None)
    except Exception as exc:
        logger.exception(
            "Oxidized blame prefetch failed for node=%s group=%s",
            node,
            group or None,
        )
        return f"Error: {exc}"

    enriched = []
    for ver in versions:
        oid = ver.get("oid") or ver.get("id", "")
        try:
            text = client.fetch_version(node, oid, group or None)
            ver["_config_lines"] = text.splitlines()
        except Exception:
            logger.exception(
                "Oxidized historical config fetch failed for node=%s group=%s oid=%s",
                node,
                group or None,
                oid,
            )
            ver["_config_lines"] = None
        enriched.append(ver)

    annotated = blame_annotate(config, enriched)
    return f"# Config with blame annotation for {node}\n\n{annotated}"


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
    relative to a reference version number.
    Changed lines are marked [+] (added/changed) or [-] (removed).
    Unchanged lines are marked [ ].
    Useful for: 'show the current config and highlight what changed since version 12'.

    Args:
        node:          device hostname or IP
        ref_version:   version number to compare against (1 = oldest, higher = newer)
        group:         device group in Oxidized (optional)
        context_lines: if > 0, only show this many unchanged lines around changes
    """
    try:
        current = client.fetch_config(node, group or None)
        versions = client.get_versions(node, group or None)
    except Exception as exc:
        logger.exception(
            "Oxidized inline diff prefetch failed for node=%s group=%s",
            node,
            group or None,
        )
        return f"Error: {exc}"

    idx = len(versions) - ref_version
    if idx < 0 or idx >= len(versions):
        return (
            f"Version {ref_version} does not exist. "
            f"Available versions: 1 to {len(versions)}."
        )

    ver = versions[idx]
    oid = ver.get("oid") or ver.get("id", "")
    try:
        ref_config = client.fetch_version(node, oid, group or None)
    except Exception as exc:
        logger.exception(
            "Oxidized reference config fetch failed for node=%s group=%s version=%s oid=%s",
            node,
            group or None,
            ref_version,
            oid,
        )
        return f"Error fetching version {ref_version}: {exc}"

    result = inline_diff(ref_config, current, context_lines=context_lines)
    ref_date = (ver.get("date") or ver.get("time") or "")[:10]
    return (
        f"# Inline diff for {node}: current vs version {ref_version} ({ref_date})\n"
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
    Return a unified diff between two historical versions of a device configuration.
    Context lines before and after each change block are configurable.
    Useful for questions like: 'show the diff between version 12 and version 14 with 10 lines of context'.

    Args:
        node:          device hostname or IP
        version_a:     first version number (older)
        version_b:     second version number (newer)
        group:         device group in Oxidized (optional)
        context_lines: number of unchanged lines to show around each change block
    """
    try:
        versions = client.get_versions(node, group or None)
    except Exception as exc:
        logger.exception(
            "Oxidized version list lookup failed for node=%s group=%s",
            node,
            group or None,
        )
        return f"Error fetching versions: {exc}"

    total = len(versions)

    def fetch_ver(num: int):
        idx = total - num
        if idx < 0 or idx >= total:
            raise ValueError(
                f"Version {num} does not exist. Available: 1 to {total}."
            )
        ver = versions[idx]
        oid = ver.get("oid") or ver.get("id", "")
        text = client.fetch_version(node, oid, group or None)
        return text, ver.get("date") or ver.get("time") or ""

    try:
        text_a, date_a = fetch_ver(version_a)
        text_b, date_b = fetch_ver(version_b)
    except ValueError as exc:
        return str(exc)
    except Exception as exc:
        logger.exception(
            "Oxidized historical diff fetch failed for node=%s group=%s versions=%s,%s",
            node,
            group or None,
            version_a,
            version_b,
        )
        return f"Error fetching config: {exc}"

    diff = unified_diff(
        text_a,
        text_b,
        old_label=f"v{version_a} ({date_a[:10]})",
        new_label=f"v{version_b} ({date_b[:10]})",
        context_lines=context_lines,
    )

    if not diff.strip():
        return (
            f"No differences between version {version_a} ({date_a[:10]}) "
            f"and version {version_b} ({date_b[:10]})."
        )

    return (
        f"# Diff for {node}: v{version_a} ({date_a[:10]}) -> v{version_b} ({date_b[:10]})\n"
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
