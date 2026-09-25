#!/usr/bin/env python3
"""Expose Codex rate limits as a read-only MCP tool over stdio."""

from __future__ import annotations

import json
import pathlib
import sys
from typing import Any


SERVER_DIR = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(SERVER_DIR))

import codex_limits_core as limits  # noqa: E402


SERVER_NAME = "codex-limits"
SERVER_VERSION = "1.0.0"
TOOL_NAME = "get_codex_limits"
SERVER_INSTRUCTIONS = (
    "For each Codex reply, call get_codex_limits once immediately before composing the final "
    "answer. Keep the answer in the language of the user's latest message, and append a compact "
    "status block in that same language. Show used percentage, a 20-cell progress bar, and the "
    "supplied local reset time for each available window. Use the 5-hour and 7-day window labels "
    "naturally in the user's language. Do not call the tool more than once per assistant turn."
)


def send(message: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def response(request_id: Any, result: dict[str, Any]) -> None:
    send({"jsonrpc": "2.0", "id": request_id, "result": result})


def error(request_id: Any, code: int, message: str) -> None:
    send({"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}})


def tool_result() -> dict[str, Any]:
    try:
        data = limits.normalize_status(limits.rpc_request(limits.codex_executable()))
    except limits.CodexLimitsError as exc:
        return {
            "isError": True,
            "content": [{"type": "text", "text": f"Could not read Codex rate limits: {exc}"}],
        }
    except Exception:
        return {
            "isError": True,
            "content": [{"type": "text", "text": "Could not read fresh Codex rate limits."}],
        }

    return {
        "content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False)}],
        "structuredContent": data,
    }


def handle(message: dict[str, Any]) -> None:
    method = message.get("method")
    request_id = message.get("id")
    params = message.get("params") or {}

    if method == "initialize":
        requested_version = params.get("protocolVersion")
        response(
            request_id,
            {
                "protocolVersion": requested_version or "2024-11-05",
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                "instructions": SERVER_INSTRUCTIONS,
            },
        )
    elif method == "ping":
        response(request_id, {})
    elif method == "tools/list":
        response(
            request_id,
            {
                "tools": [
                    {
                        "name": TOOL_NAME,
                        "title": "Read Codex rate limits",
                        "description": (
                            "Read current Codex plan rate-limit windows. Use once immediately "
                            "before composing a Codex reply that should end with a fresh status "
                            "block in the user's language. Returns locale-neutral structured "
                            "data. Read-only."
                        ),
                        "inputSchema": {
                            "type": "object",
                            "properties": {},
                            "additionalProperties": False,
                        },
                        "outputSchema": {
                            "type": "object",
                            "properties": {
                                "updatedAtLocal": {"type": "string"},
                                "timeZone": {"type": "string"},
                                "primary": {"anyOf": [{"$ref": "#/$defs/window"}, {"type": "null"}]},
                                "secondary": {"anyOf": [{"$ref": "#/$defs/window"}, {"type": "null"}]},
                            },
                            "required": ["updatedAtLocal", "timeZone", "primary", "secondary"],
                            "additionalProperties": False,
                            "$defs": {
                                "window": {
                                    "type": "object",
                                    "properties": {
                                        "windowDurationMins": {"type": ["integer", "null"]},
                                        "usedPercent": {"type": "number"},
                                        "resetsAt": {"type": ["integer", "number", "null"]},
                                        "resetTimeLocal": {"type": ["string", "null"]},
                                    },
                                    "required": [
                                        "windowDurationMins",
                                        "usedPercent",
                                        "resetsAt",
                                        "resetTimeLocal",
                                    ],
                                    "additionalProperties": False,
                                }
                            },
                        },
                        "annotations": {
                            "readOnlyHint": True,
                            "destructiveHint": False,
                            "openWorldHint": False,
                        },
                    }
                ]
            },
        )
    elif method == "tools/call":
        if params.get("name") != TOOL_NAME:
            response(request_id, {"isError": True, "content": [{"type": "text", "text": "Unknown tool."}]})
        else:
            response(request_id, tool_result())
    elif method and method.startswith("notifications/"):
        return
    elif request_id is not None:
        error(request_id, -32601, f"Method not found: {method}")


def main() -> int:
    for raw_line in sys.stdin.buffer:
        try:
            message = json.loads(raw_line)
            if isinstance(message, dict):
                handle(message)
        except (UnicodeDecodeError, json.JSONDecodeError):
            print("Ignoring malformed MCP JSON-RPC input.", file=sys.stderr)
        except Exception as exc:
            print(f"MCP server error: {type(exc).__name__}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
