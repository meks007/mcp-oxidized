# mcp-oxidized

MCP server for [Oxidized](https://github.com/ytti/oxidized) - network device configuration backup tool.

Exposes Oxidized data as MCP tools so that LLM assistants can query, compare
and analyse network device configurations via natural language.

## Features

| Tool | Description |
|---|---|
| `list_devices` | List all devices managed by Oxidized |
| `get_device_status` | Show backup status and last run details for a device |
| `get_device_versions` | List all stored configuration versions for a device |
| `get_device_config_with_blame` | Show current config with per-line version annotation |
| `get_config_with_inline_diff` | Show current config with inline change markers vs a reference version |
| `get_diff_between_versions` | Unified diff between two historical versions with configurable context lines |

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
- "List all stored versions for HFGBF253-IT in the Firewalls group."
- "Show me the current config of router01 and annotate each line with the version that last changed it."
- "Show me the current config of switch02 and highlight all changes compared to version 12."
- "Show the diff between version 12 and version 14 for firewall01 with 10 lines of context."

## Development

```bash
pip install -e ".[dev]"
python -m mcp_oxidized.server
```

## License

MIT
