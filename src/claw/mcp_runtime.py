"""MCP runtime for CodeAgent."""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .hook_policy import RuntimeBase


@dataclass
class MCPResource:
    """An MCP resource."""
    uri: str
    name: str
    path: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "uri": self.uri,
            "name": self.name,
            "path": self.path,
        }


@dataclass
class MCPServer:
    """An MCP server configuration."""
    name: str
    transport: str = "stdio"
    command: Optional[str] = None
    args: List[str] = field(default_factory=list)
    cwd: Optional[str] = None
    env: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "transport": self.transport,
            "command": self.command,
            "args": self.args,
            "cwd": self.cwd,
            "env": self.env,
        }


@dataclass
class MCPConfig:
    """MCP configuration."""
    name: str = ""
    resources: List[MCPResource] = field(default_factory=list)
    servers: List[MCPServer] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "resources": [r.to_dict() for r in self.resources],
            "servers": [s.to_dict() for s in self.servers],
        }


class MCPRuntime(RuntimeBase):
    """MCP protocol integration runtime.

    Discovery paths:
    - .claw-mcp.json
    - .mcp.json
    - .codex-mcp.json
    - mcp.json
    """

    CONFIG_FILES = [".claw-mcp.json", ".mcp.json", ".codex-mcp.json", "mcp.json"]

    def __init__(self, cwd: str):
        self.cwd = cwd
        self.config = self._discover()

    def _discover(self) -> Optional[MCPConfig]:
        """Discover MCP configuration."""
        for filename in self.CONFIG_FILES:
            filepath = os.path.join(self.cwd, filename)
            if os.path.exists(filepath):
                try:
                    with open(filepath, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    return self._parse_config(data)
                except (json.JSONDecodeError, OSError):
                    continue
        return None

    def _parse_config(self, data: Dict[str, Any]) -> MCPConfig:
        """Parse MCP configuration."""
        resources = []
        for r in data.get("resources", []):
            resources.append(MCPResource(
                uri=r.get("uri", ""),
                name=r.get("name", ""),
                path=r.get("path"),
            ))

        servers = []
        for s in data.get("servers", []):
            servers.append(MCPServer(
                name=s.get("name", ""),
                transport=s.get("transport", "stdio"),
                command=s.get("command"),
                args=s.get("args", []),
                cwd=s.get("cwd"),
                env=s.get("env", {}),
            ))

        return MCPConfig(
            name=data.get("name", ""),
            resources=resources,
            servers=servers,
        )

    def get_state(self) -> Optional[Dict[str, Any]]:
        """Get current state."""
        if self.config:
            return self.config.to_dict()
        return None

    def list_resources(self) -> List[Dict[str, Any]]:
        """List MCP resources."""
        if self.config:
            return [r.to_dict() for r in self.config.resources]
        return []

    def list_servers(self) -> List[Dict[str, Any]]:
        """List MCP servers."""
        if self.config:
            return [s.to_dict() for s in self.config.servers]
        return []

    def render_summary(self) -> str:
        """Render summary for context injection."""
        if not self.config:
            return "No MCP configuration found."

        parts = [f"[MCP: {self.config.name}]" if self.config.name else "[MCP]"]
        if self.config.resources:
            parts.append(f"Resources: {len(self.config.resources)}")
        if self.config.servers:
            parts.append(f"Servers: {len(self.config.servers)}")

        return " | ".join(parts)

    def get_prompt_guidance(self) -> str:
        """Get guidance for system prompt."""
        if not self.config or not self.config.resources:
            return ""

        return "MCP resources are available for use."


class MCPClient:
    """MCP client that manages a server subprocess and communicates via JSON-RPC over stdio.

    Each MCP server gets one subprocess. Communication happens over stdin/stdout
    using JSON-RPC 2.0 protocol. Tools are discovered via tools/list and invoked
    via tools/call.
    """

    MCP_TIMEOUT = 30  # seconds
    MCP_INIT_TIMEOUT = 10  # seconds for initialize handshake

    def __init__(self, server: MCPServer, server_cwd: str):
        self.server = server
        self.server_cwd = server_cwd
        self.process: Optional[subprocess.Popen] = None
        self._request_id = 0
        self._lock = threading.Lock()
        self._tools: List[Dict[str, Any]] = []
        self._initialized = False
        self._session_name = f"mcp__{server.name}__{uuid.uuid4().hex[:8]}"

    def start(self) -> bool:
        """Start the MCP server subprocess via stdio.

        Returns True if the server started and initialized successfully.
        """
        if not self.server.command:
            return False

        try:
            cmd = [self.server.command] + self.server.args
            env = os.environ.copy()
            if self.server.env:
                env.update(self.server.env)

            cwd = self.server.cwd or self.server_cwd

            self.process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                cwd=cwd,
                env=env,
            )
        except Exception:
            self.process = None
            return False

        # Initialize the MCP connection
        try:
            self._initialized = self.initialize()
        except Exception:
            self._initialized = False

        return self._initialized

    def stop(self):
        """Terminate the MCP server subprocess."""
        if self.process:
            try:
                self.process.stdin.close()
                self.process.stdout.close()
                self.process.stderr.close()
            except Exception:
                pass
            try:
                self.process.terminate()
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    self.process.kill()
                except Exception:
                    pass
            except Exception:
                pass
            self.process = None
        self._initialized = False
        self._tools = []

    def _next_id(self) -> int:
        with self._lock:
            self._request_id += 1
            return self._request_id

    def _send_request(self, method: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Send a JSON-RPC 2.0 request and return the response.

        Raises RuntimeError on communication failure or error response.
        """
        if not self.process or self.process.poll() is not None:
            raise RuntimeError(f"MCP server '{self.server.name}' is not running")

        request_id = self._next_id()
        request = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params or {},
        }

        try:
            request_str = json.dumps(request) + "\n"
            with self._lock:
                self.process.stdin.write(request_str)
                self.process.stdin.flush()
                response_line = self.process.stdout.readline()
        except (BrokenPipeError, OSError) as e:
            self.stop()
            raise RuntimeError(f"MCP server '{self.server.name}' communication error: {e}")

        if not response_line:
            raise RuntimeError(f"MCP server '{self.server.name}' returned empty response")

        try:
            response = json.loads(response_line)
        except json.JSONDecodeError as e:
            raise RuntimeError(f"MCP server '{self.server.name}' returned invalid JSON: {e}")

        if "error" in response:
            error = response["error"]
            raise RuntimeError(
                f"MCP error from '{self.server.name}': {error.get('message', str(error))}"
            )

        return response.get("result", {})

    def initialize(self) -> bool:
        """Send initialize request to negotiate protocol version.

        Returns True on successful initialization.
        """
        try:
            result = self._send_request("initialize", {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {
                    "name": "claw-code-agent",
                    "version": "1.0.0",
                },
            })
            # Send initialized notification
            if self.process and self.process.poll() is None:
                try:
                    notification = json.dumps({
                        "jsonrpc": "2.0",
                        "method": "notifications/initialized",
                    }) + "\n"
                    with self._lock:
                        self.process.stdin.write(notification)
                        self.process.stdin.flush()
                except Exception:
                    pass
            return "protocolVersion" in result or True  # Server acknowledged
        except RuntimeError:
            return False

    def list_tools(self) -> List[Dict[str, Any]]:
        """Send tools/list and return the list of available tools."""
        try:
            result = self._send_request("tools/list")
            tools = result.get("tools", [])
            self._tools = tools
            return tools
        except RuntimeError:
            return []

    def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Send tools/call and return the tool execution result.

        Args:
            tool_name: Name of the MCP tool to call
            arguments: Tool arguments

        Returns:
            Dict with tool result, or error info on failure
        """
        try:
            result = self._send_request("tools/call", {
                "name": tool_name,
                "arguments": arguments,
            })
            return {"ok": True, "result": result}
        except RuntimeError as e:
            return {"ok": False, "error": str(e)}

    def get_tools(self) -> List[Dict[str, Any]]:
        """Get cached list of tools (call list_tools() first)."""
        return self._tools

    @property
    def is_running(self) -> bool:
        return self.process is not None and self.process.poll() is None and self._initialized


# Global registry of MCP clients keyed by server name
_mcp_clients: Dict[str, MCPClient] = {}


def get_mcp_client(server_name: str) -> Optional[MCPClient]:
    """Get a running MCP client by server name."""
    return _mcp_clients.get(server_name)


def register_mcp_tools_in_registry(server_name: str, tools: List[Dict[str, Any]]):
    """Register MCP tools from a server into the global tool registry.

    Each MCP tool is registered with the name format: mcp__{server_name}__{tool_name}
    """
    from .agent_tools import default_tool_registry, AgentTool, _mcp_tool

    registry = default_tool_registry()

    for tool in tools:
        tool_name = tool.get("name", "")
        full_name = f"mcp__{server_name}__{tool_name}"

        # Build JSON Schema from MCP tool's inputSchema
        input_schema = tool.get("inputSchema", {})
        parameters = {
            "type": "object",
            "properties": input_schema.get("properties", {}),
        }
        if "required" in input_schema:
            parameters["required"] = input_schema["required"]

        # Register with a wrapper handler that captures server_name and tool_name
        def make_handler(srv_name: str, tl_name: str):
            def handler(**kwargs) -> Dict[str, Any]:
                return _mcp_tool(tl_name, srv_name, kwargs)
            return handler

        registry.register(AgentTool(
            name=full_name,
            description=tool.get("description", f"MCP tool: {tool_name}"),
            parameters=parameters,
            handler=make_handler(server_name, tool_name),
            tags=["mcp", server_name],
        ))


def start_mcp_servers(runtime: MCPRuntime, cwd: str) -> Dict[str, MCPClient]:
    """Start all configured MCP servers and discover their tools.

    Args:
        runtime: MCPRuntime instance with discovered config
        cwd: Working directory for relative paths

    Returns:
        Dict mapping server name to MCPClient instance
    """
    if not runtime.config or not runtime.config.servers:
        return {}

    clients: Dict[str, MCPClient] = {}

    for server in runtime.config.servers:
        if server.transport != "stdio":
            continue  # Only stdio transport is supported currently

        client = MCPClient(server, cwd)
        if client.start():
            # Discover tools
            tools = client.list_tools()
            if tools:
                try:
                    register_mcp_tools_in_registry(server.name, tools)
                except Exception:
                    pass  # Tool registration failure is non-fatal
            clients[server.name] = client
            _mcp_clients[server.name] = client
        # Start failure is non-fatal; server is simply unavailable

    return clients


def stop_all_mcp_clients():
    """Stop all running MCP clients."""
    for name, client in list(_mcp_clients.items()):
        try:
            client.stop()
        except Exception:
            pass
    _mcp_clients.clear()