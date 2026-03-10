"""pdsmcp — NASA PDS Planetary Plasma Interactions data access.

Install as: pip install xhelio-pds
For MCP server: pip install xhelio-pds[mcp]
"""

__version__ = "0.2.0"

from pdsmcp.config import configure  # noqa: F401


def main():
    """Entry point for the MCP server (xhelio-pds-mcp command).

    Requires the [mcp] extra: pip install xhelio-pds[mcp]
    """
    try:
        from pdsmcp.server import serve
    except ImportError:
        print(
            "Error: MCP server requires the 'mcp' package.\n"
            "Install with: pip install xhelio-pds[mcp]"
        )
        raise SystemExit(1)
    serve()
