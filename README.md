# mcp-oxidized

MCP server for [Oxidized](https://github.com/ytti/oxidized), a network device configuration backup tool.

It exposes Oxidized data as MCP tools and resources, allowing assistants to query,
compare, analyze, and retrieve network device configurations.

## Features

| Capability | Description |
|---|---|
| `list_devices` | List all devices managed by Oxidized |
| `find_devices` | Find devices by exact or partial name, full name, or IP address |
| `get_device_status` | Show backup status and last run details for a device |
| `get_device_versions` | List stored configuration versions for a device |
| `prepare_configs` | Select one or more devices for the current-config resource |
| `oxidized://config/get_contents` | Retrieve all current configurations selected by `prepare_configs` |
| `prepare_blame` | Select a device for the static blame resource |
| `oxidized://config/get_blame` | Retrieve the configuration blame prepared by `prepare_blame` |
| `get_config_with_inline_diff` | Show current config with inline change markers against a version |
| `get_diff_between_versions` | Unified diff between two historical versions with configurable context |

All device-specific tools accept an exact device name or a unique partial name.
Matching is case-insensitive. If a partial name matches multiple devices, the
server returns candidates and asks for a more specific name or group.

## Prepared configuration resources

Current configurations can be too large for a tool response. Use `prepare_configs`
to resolve one or more devices, then read the static multi-content resource. The
prepare tool returns device metadata only and does not load configuration data.

```text
prepare_configs(nodes=["router01"], group="Core")
read oxidized://config/get_contents

prepare_configs(nodes=["router01", "switch02"], group="Core")
read oxidized://config/get_contents
```

The resource returns a separate content item for every prepared device. Each item
has a unique `oxidized://config/<group>/<node>` URI and uses this MIME type:

```text
text/plain; charset=utf-8
```

The client must send the same Bearer token with both the prepare call and the
later resource read:

```http
Authorization: Bearer <long-random-token>
```

The server hashes this token and uses the hash only as an in-memory lookup key.
The original token is not stored or logged, and it is not validated as an
identity credential. This allows selections to survive a client opening
different MCP transport sessions for tool calls and resource reads.

Current configuration and blame selections are separate, so both resources can
refer to different devices for the same Bearer token. Resource reads fetch the
current configuration and required historical versions live from Oxidized. No
configuration, version, or blame result is cached or written to a database.

If a resource is read before its matching prepare tool, it returns instructions
for the required prepare call. A server restart clears all prepared selections.
Run one server worker because prepared selections are intentionally held only in
that process memory.

## Requirements

- Python 3.11+
- Oxidized with oxidized-web running and its REST API enabled
- Docker, recommended

## Quick start

```bash
cp .env.example .env
# Edit .env with your Oxidized URL and credentials
docker compose up -d
```

The MCP server listens on port 8000 using Streamable HTTP transport.

## Configuration

| Variable | Description | Default |
|---|---|---|
| `OXIDIZED_URL` | Base URL of the Oxidized instance | required |
| `OXIDIZED_USER` | Basic Auth username | required |
| `OXIDIZED_PASS` | Basic Auth password | required |
| `MCP_PORT` | MCP server port | 8000 |

## MCP client configuration

Configure the client to send a stable Bearer token on every MCP request:

```http
Authorization: Bearer <long-random-token>
```

The token associates `prepare_configs` and `prepare_blame` calls with later
resource reads. Treat it as a secret because a caller using the same token can
access and replace that token's prepared selections.

## Example prompts

- "List all devices and show which ones failed their last backup."
- "Find the device containing `HFGBF253` in its name."
- "Show the status of the device matching `253-IT`."
- "List stored versions for `HFGBF253-IT` in the `Firewalls` group."
- "Prepare the current configuration of router01."
- "Prepare the current configurations of router01, switch02, and firewall03."
- "Prepare configuration blame for switch02."
- "Show the current config of switch02 and highlight changes against version 12."
- "Show the diff between version 12 and version 14 for firewall01 with 10 lines of context."

## Development

```bash
pip install -e ".[dev]"
python -m mcp_oxidized.server
```

## License

MIT
