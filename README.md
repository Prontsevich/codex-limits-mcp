# Codex Limits MCP

A local, read-only MCP server that exposes the current Codex rate-limit windows as the `get_codex_limits` tool.

The server asks the installed Codex CLI to read `account/rateLimits/read` through Codex App Server. It relies on the CLI's existing sign-in and does not read token, cookie, or authentication files directly.

## Requirements

- macOS with Codex CLI installed and signed in
- Python 3.10 or newer

The server uses only the Python standard library.

## Install

Clone this repository to a stable local path. Add the following entry to `~/.codex/config.toml`, replacing the example path with the absolute path to this checkout:

```toml
[mcp_servers.codex_limits]
command = "/absolute/path/to/python3"
args = ["/absolute/path/to/codex-limits-mcp/server.py"]
enabled = true
startup_timeout_sec = 20
tool_timeout_sec = 20
```

Use a Python executable that is available on your machine. Restart MCP servers in Codex after saving the configuration. The tool should then appear as `get_codex_limits`.

## Tool behavior

`get_codex_limits` accepts an optional `percentageMode` argument: `remaining` (the default) or `used`. The result includes both `usedPercent` and `displayPercent`, and locale-neutral data for the primary and secondary rate-limit windows and reset times. Reset times are formatted in the local timezone.

The MCP server instructions make this a user-requested tool. Codex should not call it automatically or append limit information to replies unless the user asks for it.

## Data source

The server starts `codex app-server --listen stdio://`, initializes the JSON-RPC connection, then calls the documented `account/rateLimits/read` method. It selects the Codex rate-limit bucket and formats its primary and secondary windows.

No rate-limit snapshots are written to disk.
