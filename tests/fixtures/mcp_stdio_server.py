"""Disposable local MCP server used only by integration tests."""

from mcp.server import MCPServer


server = MCPServer("ISABELLA local test")


@server.tool()
def echo(text: str) -> dict[str, str]:
    """Return text unchanged."""
    return {"echo": text}


if __name__ == "__main__":
    server.run(transport="stdio")
