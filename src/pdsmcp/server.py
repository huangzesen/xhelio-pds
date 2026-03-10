"""MCP server — exposes PDS PPI tools via Model Context Protocol.

Requires the [mcp] extra: pip install xhelio-pds[mcp]
"""

import json
import logging
from pathlib import Path

import pandas as pd

try:
    from mcp.server.fastmcp import FastMCP
except ImportError:
    raise ImportError(
        "MCP server requires the 'mcp' package. "
        "Install with: pip install xhelio-pds[mcp]"
    )

from pdsmcp.catalog import browse_missions as _browse_missions
from pdsmcp.prompts import build_mission_prompt
from pdsmcp.metadata import browse_parameters as _browse_parameters
from pdsmcp.fetch import fetch_data as _fetch_data, compute_stats

logger = logging.getLogger(__name__)


def create_server() -> FastMCP:
    """Create and configure the MCP server with all tools."""
    mcp = FastMCP(
        "pdsmcp",
        instructions=(
            "MCP server for NASA PDS Planetary Plasma Interactions — "
            "browse missions, inspect parameters, fetch PDS data"
        ),
    )

    @mcp.tool()
    def browse_missions(query: str | None = None) -> str:
        """List all available PDS PPI missions with descriptions, dataset counts, and instrument names.

        Call this first to discover what missions are available. Returns a JSON array
        of mission summaries. Optionally filter by keyword.

        Args:
            query: Optional keyword to filter missions (e.g., 'jupiter', 'magnetic').
        """
        missions = _browse_missions(query=query)
        return json.dumps(missions, indent=2)

    @mcp.tool()
    def load_mission(mission_id: str) -> str:
        """Load the complete system prompt for a PDS PPI mission.

        Returns a detailed text prompt containing:
        - Role instructions for acting as a PDS data specialist
        - PDS-specific workflow (how to discover and fetch data)
        - Full dataset catalog for this mission (instruments, dataset IDs, descriptions, time coverage)

        Use the returned text as context/instructions to work with this mission's data.

        Args:
            mission_id: Mission identifier — use the lowercase stem from browse_missions
                        (e.g., 'juno', 'cassini', 'voyager1', 'maven').
        """
        return build_mission_prompt(mission_id)

    @mcp.tool()
    def browse_parameters(
        dataset_id: str | None = None,
        dataset_ids: list[str] | None = None,
    ) -> str:
        """Browse all parameters (variables) for one or more PDS datasets.

        Returns parameter metadata: name, type, units, description, fill value.
        Use this to discover what variables a dataset contains before calling fetch_data.

        Metadata is extracted from PDS label files and cached locally.

        Args:
            dataset_id: Single dataset ID (e.g., 'pds3:JNO-J-3-FGM-CAL-V1.0:DATA').
            dataset_ids: Multiple dataset IDs to query at once.
        """
        result = _browse_parameters(dataset_id=dataset_id, dataset_ids=dataset_ids)
        return json.dumps(result, indent=2)

    @mcp.tool()
    def fetch_data(
        dataset_id: str,
        parameter_id: str,
        start: str,
        stop: str,
        format: str = "csv",
        output_dir: str | None = None,
    ) -> str:
        """Fetch timeseries data from NASA PDS PPI archive, write to a file, return metadata + stats.

        Downloads PDS data files (fixed-width ASCII tables), extracts the requested
        parameter, writes the data to a file on disk, and returns rich metadata
        including per-column statistics (min, max, mean, std, nan_ratio).

        The data is NOT returned inline — read the file at the returned path.

        Args:
            dataset_id: PDS dataset ID — PDS4 URN (e.g., 'urn:nasa:pds:cassini-mag-cal:data-1sec-krtp')
                        or PDS3 (e.g., 'pds3:JNO-J-3-FGM-CAL-V1.0:DATA').
            parameter_id: Parameter name to fetch (e.g., 'BR', 'BX PLANETOCENTRIC').
                          Use browse_parameters to discover available parameters.
            start: Start time in ISO 8601 format (e.g., '2024-01-01').
            stop: End time in ISO 8601 format (e.g., '2024-01-07').
            format: Output file format — 'csv' (default) or 'json'.
            output_dir: Directory for output file. Defaults to system temp dir.
        """
        import tempfile
        from datetime import datetime

        # Call the library function — returns dict with DataFrame
        lib_result = _fetch_data(
            dataset_id=dataset_id,
            parameter_id=parameter_id,
            start=start,
            stop=stop,
        )

        df = lib_result["data"]
        stats = lib_result.get("stats") or compute_stats(df)

        out_dir = Path(output_dir) if output_dir else Path(tempfile.gettempdir())
        out_dir.mkdir(parents=True, exist_ok=True)
        suffix = datetime.now().strftime("%Y%m%d_%H%M%S")

        # Write to file
        safe_ds = dataset_id.replace(":", "_").replace("/", "_")
        safe_param = parameter_id.replace(" ", "_")
        file_path = out_dir / f"{safe_ds}_{safe_param}_{suffix}.{format}"

        if format == "json":
            data = {
                "time": df.index.strftime("%Y-%m-%dT%H:%M:%S.%f").tolist(),
            }
            for col in df.columns:
                data[str(col)] = [
                    None if pd.isna(v) else v for v in df[col].tolist()
                ]
            with open(file_path, "w") as f:
                json.dump(data, f)
        else:
            df.to_csv(file_path)

        return json.dumps({
            "status": "success",
            "file_path": str(file_path),
            "format": format,
            "dataset_id": dataset_id,
            "parameter_id": parameter_id,
            "time_range": {"start": start, "stop": stop},
            "total_rows": len(df),
            "units": lib_result.get("units", ""),
            "description": lib_result.get("description", ""),
            "stats": stats,
        }, indent=2, default=str)

    return mcp


def serve():
    """Run the MCP server (stdio transport)."""
    logging.basicConfig(level=logging.INFO)
    server = create_server()
    server.run()
