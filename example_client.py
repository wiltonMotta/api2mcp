"""Example client for the SCNet OpenAPI MCP server (StreamableHTTP).

Connects to a running server at http://localhost:8000/mcp/{username}.
The user must have already authenticated via /auth/{username}.

Usage:
    python example_client.py
    python example_client.py --user ac1npa3sf2
    python example_client.py --host localhost --port 8000 --user myuser
"""

from __future__ import annotations

import asyncio
import json
import sys

from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport


def _print_result(label: str, result) -> None:
    print(f"\n=== {label} ===")
    payload = result.data
    if payload is None and result.content:
        try:
            payload = json.loads(result.content[0].text)
        except (ValueError, AttributeError):
            payload = [getattr(c, "text", str(c)) for c in result.content]
    print(json.dumps(payload, indent=2, ensure_ascii=False)[:1200])


async def demo(username: str, host: str, port: int) -> None:
    url = f"http://{host}:{port}/mcp/{username}"
    transport = StreamableHttpTransport(url)
    client = Client(transport)

    async with client:
        tools = await client.list_tools()
        print(f"Server exposes {len(tools)} tool(s):")
        for tool in tools:
            required = (tool.inputSchema or {}).get("required", [])
            props = list((tool.inputSchema or {}).get("properties", {}).keys())
            print(f"  - {tool.name}({', '.join(props)})  required={required}")

        # Test SCNet tools
        if any(t.name == "get_user_info" for t in tools):
            _print_result(
                "get_user_info()",
                await client.call_tool("get_user_info", {}),
            )

        if any(t.name == "list_available_partitions" for t in tools):
            _print_result(
                "list_available_partitions()",
                await client.call_tool("list_available_partitions", {}),
            )


async def main() -> None:
    args = iter(sys.argv[1:])
    kwargs = {
        "username": "ac1npa3sf2",
        "host": "localhost",
        "port": 8000,
    }
    for arg in args:
        if arg == "--user":
            kwargs["username"] = next(args, kwargs["username"])
        elif arg == "--host":
            kwargs["host"] = next(args, kwargs["host"])
        elif arg == "--port":
            kwargs["port"] = int(next(args, str(kwargs["port"])))

    print(f"Connecting to http://{kwargs['host']}:{kwargs['port']}/mcp/{kwargs['username']}")
    await demo(**kwargs)


if __name__ == "__main__":
    asyncio.run(main())
