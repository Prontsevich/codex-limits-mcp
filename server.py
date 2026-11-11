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
SERVER_VERSION = "1.1.0"
TOOL_NAME = "get_codex_limits"
PERCENTAGE_MODES = {"used", "remaining"}
SERVER_INSTRUCTIONS = (
    "Use get_codex_limits only when the user explicitly asks about their Codex rate limits or "
    "requests the current limit data. Do not call it automatically for replies, and do not append "
    "a rate-limit status block or other limit information to replies unless the user asks for it. "
    "The percentageMode defaults to 'remaining'; pass 'used' only when the user asks for the "
    "used percentage."
)


def send(message: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def response(request_id: Any, result: dict[str, Any]) -> None:
    send({"jsonrpc": "2.0", "id": request_id, "result": result})


def error(request_id: Any, code: int, message: str) -> None:
    send({"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}})


def tool_result(arguments: Any) -> dict[str, Any]:
    if not isinstance(arguments, dict):
        arguments = {}
    percentage_mode = arguments.get("percentageMode", "remaining")
    if not isinstance(percentage_mode, str) or percentage_mode not in PERCENTAGE_MODES:
        return {
            "isError": True,
            "content": [{"type": "text", "text": "percentageMode must be 'used' or 'remaining'."}],
        }

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

    data["percentageMode"] = percentage_mode
    for window in (data["primary"], data["secondary"]):
        if window is None:
            continue
        used = window["usedPercent"]
        window["displayPercent"] = used if percentage_mode == "used" else 100 - used

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
                            "Read current Codex plan rate-limit windows when explicitly requested. "
                            "Do not call automatically or append limit data to replies. "
                            "percentageMode selects whether "
                            "displayPercent represents used or remaining usage (defaults to remaining). "
                            "Returns locale-neutral structured data. Read-only."
                        ),
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "percentageMode": {
                                    "type": "string",
                                    "enum": ["used", "remaining"],
                                    "default": "remaining",
                                    "description": "Show the percentage used, or the percentage remaining.",
                                }
                            },
                            "additionalProperties": False,
                        },
                        "outputSchema": {
                            "type": "object",
                            "properties": {
                                "updatedAtLocal": {"type": "string"},
                                "timeZone": {"type": "string"},
                                "percentageMode": {"type": "string", "enum": ["used", "remaining"]},
                                "primary": {"anyOf": [{"$ref": "#/$defs/window"}, {"type": "null"}]},
                                "secondary": {"anyOf": [{"$ref": "#/$defs/window"}, {"type": "null"}]},
                            },
                            "required": ["updatedAtLocal", "timeZone", "percentageMode", "primary", "secondary"],
                            "additionalProperties": False,
                            "$defs": {
                                "window": {
                                    "type": "object",
                                    "properties": {
                                        "windowDurationMins": {"type": ["integer", "null"]},
                                        "usedPercent": {"type": "number"},
                                        "displayPercent": {"type": "number"},
                                        "resetsAt": {"type": ["integer", "number", "null"]},
                                        "resetTimeLocal": {"type": ["string", "null"]},
                                    },
                                    "required": [
                                        "windowDurationMins",
                                        "usedPercent",
                                        "displayPercent",
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
            response(request_id, tool_result(params.get("arguments")))
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
