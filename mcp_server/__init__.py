"""MCP server exposing the agentic NL2SQL pipeline as callable tools.

Deliberately empty of logic. Importing `mcp_server` should never be expensive:
the server in `server.py` is a thin HTTP client, and keeping the package
__init__ bare means `python -m mcp_server.server` pays no import cost beyond
the MCP SDK and httpx.
"""
