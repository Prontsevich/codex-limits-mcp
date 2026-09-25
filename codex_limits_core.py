#!/usr/bin/env python3
"""Read and format Codex rate limits through the local app-server API."""

from __future__ import annotations

import datetime as dt
import json
import math
import os
import pathlib
import select
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Any


TIMEOUT_SECONDS = 8.0
MAX_LINE_BYTES = 1_048_576


class CodexLimitsError(Exception):
    pass


def codex_executable() -> str:
    candidates = [
        shutil.which("codex"),
        str(pathlib.Path.home() / ".local/bin/codex"),
        str(pathlib.Path.home() / ".local/share/mise/shims/codex"),
        "/opt/homebrew/bin/codex",
        "/usr/local/bin/codex",
        "/usr/bin/codex",
    ]
    for candidate in candidates:
        if candidate and os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    raise CodexLimitsError("Codex CLI was not found")


def rpc_request(executable: str) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="codex-limits-hook-") as temp_dir:
        env = os.environ.copy()
        env["PWD"] = temp_dir
        env.pop("OLDPWD", None)
        env.pop("INIT_CWD", None)
        try:
            process = subprocess.Popen(
                [executable, "app-server", "--listen", "stdio://"],
                cwd=temp_dir,
                env=env,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
        except OSError as exc:
            raise CodexLimitsError("Could not start the Codex app server") from exc

        buffer = bytearray()

        def send(message: dict[str, Any]) -> None:
            assert process.stdin is not None
            process.stdin.write(json.dumps(message, separators=(",", ":")).encode("utf-8") + b"\n")
            process.stdin.flush()

        def read_message(deadline: float) -> dict[str, Any]:
            assert process.stdout is not None
            fd = process.stdout.fileno()
            while True:
                newline = buffer.find(b"\n")
                if newline >= 0:
                    line = bytes(buffer[:newline]).strip()
                    del buffer[: newline + 1]
                    if not line:
                        continue
                    try:
                        message = json.loads(line)
                    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                        raise CodexLimitsError("Codex app server returned an invalid JSON-RPC response") from exc
                    if isinstance(message, dict):
                        return message
                    continue

                if len(buffer) > MAX_LINE_BYTES:
                    raise CodexLimitsError("Codex app server response exceeded the size limit")

                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise CodexLimitsError("Codex app server request timed out")
                readable, _, _ = select.select([fd], [], [], remaining)
                if not readable:
                    raise CodexLimitsError("Codex app server request timed out")
                chunk = os.read(fd, 65_536)
                if not chunk:
                    raise CodexLimitsError("Codex app server exited before responding")
                buffer.extend(chunk)

        try:
            send(
                {
                    "method": "initialize",
                    "id": 1,
                    "params": {
                        "clientInfo": {
                            "name": "codex-limits-stop-hook",
                            "title": "Codex Limits Stop Hook",
                            "version": "1.0.0",
                        }
                    },
                }
            )

            deadline = time.monotonic() + TIMEOUT_SECONDS
            while True:
                message = read_message(deadline)
                if message.get("id") != 1:
                    continue
                if message.get("error"):
                    raise classify_rpc_error(message["error"])
                if "result" not in message:
                    raise CodexLimitsError("Unexpected Codex app server initialization response")
                break

            send({"method": "initialized", "params": {}})
            send({"method": "account/rateLimits/read", "id": 2})

            deadline = time.monotonic() + TIMEOUT_SECONDS
            while True:
                message = read_message(deadline)
                if message.get("id") != 2:
                    continue
                if message.get("error"):
                    raise classify_rpc_error(message["error"])
                result = message.get("result")
                if not isinstance(result, dict):
                    raise CodexLimitsError("Codex app server did not return rate-limit data")
                return result
        finally:
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=1.0)
            if process.stdin is not None:
                process.stdin.close()
            if process.stdout is not None:
                process.stdout.close()


def classify_rpc_error(error: Any) -> CodexLimitsError:
    message = error.get("message", "") if isinstance(error, dict) else ""
    lowered = str(message).lower()
    if any(term in lowered for term in ("auth", "login", "unauthorized", "not logged")):
        return CodexLimitsError("Codex CLI is not signed in")
    if any(term in lowered for term in ("unknown method", "requires experimentalapi", "-32601")):
        return CodexLimitsError("This Codex CLI version does not support reading rate limits")
    return CodexLimitsError("Codex app server rejected the rate-limit request")


def select_codex_bucket(result: dict[str, Any]) -> dict[str, Any]:
    buckets = result.get("rateLimitsByLimitId")
    if isinstance(buckets, dict):
        bucket = buckets.get("codex")
        if isinstance(bucket, dict) and bucket.get("limitId", "codex") == "codex":
            return bucket
        matches = [value for value in buckets.values() if isinstance(value, dict) and value.get("limitId") == "codex"]
        if len(matches) == 1:
            return matches[0]

    bucket = result.get("rateLimits")
    if isinstance(bucket, dict) and bucket.get("limitId") == "codex":
        return bucket
    raise CodexLimitsError("Codex app server did not return the Codex rate-limit bucket")


def normalize_status(result: dict[str, Any]) -> dict[str, Any]:
    bucket = select_codex_bucket(result)
    now = dt.datetime.now().astimezone()

    def normalize_window(payload: Any) -> dict[str, Any] | None:
        if not isinstance(payload, dict):
            return None
        used = payload.get("usedPercent")
        if isinstance(used, bool) or not isinstance(used, (int, float)) or not math.isfinite(used):
            return None
        if not 0 <= used <= 100:
            return None

        reset_at = payload.get("resetsAt")
        reset_time = None
        if isinstance(reset_at, (int, float)) and not isinstance(reset_at, bool) and reset_at > 0:
            try:
                reset_time = dt.datetime.fromtimestamp(reset_at).astimezone().strftime("%H:%M")
            except (OverflowError, OSError, ValueError):
                pass

        return {
            "windowDurationMins": payload.get("windowDurationMins"),
            "usedPercent": used,
            "resetsAt": reset_at if reset_time is not None else None,
            "resetTimeLocal": reset_time,
        }

    primary = normalize_window(bucket.get("primary"))
    secondary = normalize_window(bucket.get("secondary"))
    if primary is None and secondary is None:
        raise CodexLimitsError("Codex app server did not return any rate-limit windows")

    return {
        "updatedAtLocal": now.strftime("%H:%M"),
        "timeZone": now.tzname() or "local",
        "primary": primary,
        "secondary": secondary,
    }
