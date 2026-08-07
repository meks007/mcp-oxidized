# mcp-oxidized

MCP server for [Oxidized](https://github.com/ytti/oxidized) - network device configuration backup tool.

Exposes Oxidized data as MCP tools and resources so that LLM assistants can query,
compare, analyse, and download network device configurations via natural language.

## Features

| Capability | Description |
|---|---|
| `list_devices` | List all devices managed by Oxidized |
| `find_devices` | Find devices by exact or partial name, full name, or IP address |
| `get_device_status` | Show backup status and last run details for a device |
| `get_device_versions` | List all stored configuration versions for a device |
| `prepare_config` | Select a device for the static current-config resource |
| `oxidized://config/get_content` | Download the current configuration prepared by `prepare_config` |
| `prepare_blame` | Select a device for the static blame resource |
| `oxidized://config/get_blame` | Download the annotated configuration prepared by `prepare_blame` |
| `get_config_with_inline_diff` | Show current config with inline change markers vs a reference version |
| `get_diff_between_versions` | Unified diff between two historical versions with configurable context lines |

All device-specific tools accept an exact device name or a unique partial name.
Matching is case-insensitive. If a partial name matches multiple devices, the
server returns the matching candidates and asks for a more specific name or
group instead of calling Oxidized with an ambiguous device name.

## Prepared configuration resources

Some configurations and blame results are too large for a tool response. Use the
prepare tools to resolve a device and then read the associated static resource.
The prepare tools return only device metadata and do not load configuration data.

```text
prepare_config(node="router01", group="Core")
read oxidized://config/get_content

prepare_blame(node="switch02")
read oxidized://config/get_blame
```

The server stores only the resolved node and group in memory for the current MCP
session. Current-config and blame selections are separate, so both resources can
refer to different devices in the same session. Resource reads fetch the current
configuration and all required historical versions live from Oxidized. No
configuration, version, or blame result is cached or written to a database.

If a resource is read before its matching prepare tool, it returns instructions
for the required prepare call. A server restart clears all prepared selections.
Run one server worker because prepared selections are intentionally held only in
that process memory.

## Requirements

- Python 3.11+
- Oxidized with oxidized-web running (REST API enabled)
- Docker (recommended)

## Quick Start

```bash
cp .env.example .env
# Edit .env with your Oxidized URL and credentials
docker compose up -d
```

The MCP server listens on port 8000 (Streamable HTTP transport).

## Configuration

| Variable | Description | Default |
|---|---|---|
| `OXIDIZED_URL` | Base URL of your Oxidized instance | required |
| `OXIDIZED_USER` | Basic Auth username | required |
| `OXIDIZED_PASS` | Basic Auth password | required |
| `MCP_PORT` | Port the MCP server listens on | 8000 |

## MCP Client Configuration

Add to your MCP client (e.g. Claude Desktop `mcp.json`):

```json
{
  "mcpServers": {
    "oxidized": {
      "url": "http://localhost:8000/mcp"
    }
  }
}
```

## Example Prompts

- "List all devices and show me which ones failed their last backup."
- "Find the device containing `HFGBF253` in its name."
- "Show the status of the device matching `253-IT`."
- "List all stored versions for `HFGBF253-IT` in the `Firewalls` group."
- "Prepare the current configuration of router01 for download."
- "Prepare configuration blame for switch02."
- "Show the current config of switch02 and highlight all changes compared to version 12."
- "Show the diff between version 12 and version 14 for firewall01 with 10 lines of context."

## Development

```bash
pip install -e ".[dev]"
python -m mcp_oxidized.server
```

## License

MIT
