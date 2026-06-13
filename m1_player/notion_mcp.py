from __future__ import annotations

import json
import queue
import subprocess
import sys
import threading
import time
from typing import Any


NOTION_MCP_URL = "https://mcp.notion.com/mcp"


class NotionMcpClient:
    def __init__(self, request_timeout_sec: int = 45) -> None:
        self.proc: subprocess.Popen[str] | None = None
        self.queue: queue.Queue[str] = queue.Queue()
        self.next_id = 1
        self.tools: set[str] = set()
        self.request_timeout_sec = request_timeout_sec

    def start(self) -> None:
        self.proc = subprocess.Popen(
            ["npx.cmd", "-y", "mcp-remote", NOTION_MCP_URL],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        assert self.proc.stdout is not None
        threading.Thread(target=self._reader, args=(self.proc.stdout,), daemon=True).start()
        self.request(
            "initialize",
            {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "m1-makeup-player", "version": "0.1"},
            },
        )
        self.notify("notifications/initialized", {})
        self.tools = {tool["name"] for tool in self.request("tools/list", {}).get("result", {}).get("tools", [])}

    def close(self) -> None:
        if self.proc and self.proc.poll() is None:
            if sys.platform == "win32":
                subprocess.run(
                    ["taskkill", "/PID", str(self.proc.pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )
            else:
                self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()

    def _reader(self, stream: Any) -> None:
        for line in stream:
            self.queue.put(line)

    def _send(self, payload: dict[str, Any]) -> None:
        if not self.proc or not self.proc.stdin:
            raise RuntimeError("Notion MCP client is not started")
        self.proc.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
        self.proc.stdin.flush()

    def notify(self, method: str, params: dict[str, Any]) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def request(self, method: str, params: dict[str, Any], timeout: int | None = None) -> dict[str, Any]:
        request_id = self.next_id
        self.next_id += 1
        self._send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
        timeout = timeout or self.request_timeout_sec
        end = time.time() + timeout
        while time.time() < end:
            try:
                line = self.queue.get(timeout=0.2).strip()
            except queue.Empty:
                continue
            if not line:
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            if message.get("id") == request_id:
                if "error" in message:
                    raise RuntimeError(json.dumps(message["error"], ensure_ascii=False))
                return message
        raise TimeoutError(method)

    def find_tool(self, suffix: str) -> str:
        matches = sorted(name for name in self.tools if name.endswith(suffix) or suffix in name)
        if not matches:
            raise RuntimeError(f"tool not found: {suffix}")
        return matches[0]

    def call_tool(self, suffix: str, arguments: dict[str, Any], timeout: int | None = None) -> dict[str, Any]:
        return self.request(
            "tools/call",
            {"name": self.find_tool(suffix), "arguments": arguments},
            timeout=timeout,
        )


def extract_tool_text(tool_response: dict[str, Any]) -> str:
    content = tool_response.get("result", {}).get("content", [])
    text = ""
    for item in content:
        if isinstance(item, dict) and item.get("type") == "text":
            text += str(item.get("text", ""))
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return text
    return str(parsed.get("text", text))


def extract_tool_json(tool_response: dict[str, Any]) -> dict[str, Any]:
    content = tool_response.get("result", {}).get("content", [])
    text = ""
    for item in content:
        if isinstance(item, dict) and item.get("type") == "text":
            text += str(item.get("text", ""))
    return json.loads(text)
