#!/usr/bin/env python3
"""
Claw Recall — MCP Server (SSE/HTTP transport)

Runs the same MCP server as mcp_server.py but over HTTP (SSE transport)
instead of stdio. This allows remote agents (WSL) to connect directly
via HTTP URL without SSH pipes or proxies.

Usage:
    python3 mcp_server_sse.py                    # Default: 172.17.0.1:8766

MCP client config:
    {
      "mcpServers": {
        "claw-recall": {
          "url": "http://100.82.195.86:8766/sse"
        }
      }
    }
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

# Import the mcp instance with all tools already registered
from mcp_server import mcp

if __name__ == "__main__":
    # Override host/port on the settings object before run()
    mcp.settings.host = "100.82.195.86"
    mcp.settings.port = 8766
    mcp.settings.transport_security.allowed_hosts = ["100.82.195.86:*", "127.0.0.1:*", "localhost:*"]
    mcp.settings.transport_security.allowed_origins = ["http://100.82.195.86:*", "http://127.0.0.1:*", "http://localhost:*"]

    print("Claw Recall MCP (SSE) running at http://100.82.195.86:8766/sse")
    mcp.run(transport="sse")
