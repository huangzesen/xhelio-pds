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
    def browse_missions() -> str:
        """List all available PDS PPI missions with descriptions, dataset counts, and instrument names.

        Call this first to discover what missions are available. Returns a JSON array of mission summaries.
        """
        missions = _browse_missions()
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
        parameters: list[str],
        start: str,
        stop: str,
        output_dir: str,
        format: str = "csv",
    ) -> str:
        """Fetch timeseries data from NASA PDS PPI archive, write to a file, return metadata + stats.

        Downloads PDS data files (fixed-width ASCII tables), extracts the requested
        parameters, writes the data to a file on disk, and returns rich metadata
        including per-column statistics (min, max, mean, std, nan_ratio).

        The data is NOT returned inline — read the file at the returned path.
        The caller is responsible for cleaning up the file when done.

        Args:
            dataset_id: PDS dataset ID — PDS4 URN (e.g., 'urn:nasa:pds:cassini-mag-cal:data-1sec-krtp')
                        or PDS3 (e.g., 'pds3:JNO-J-3-FGM-CAL-V1.0:DATA').
            parameters: List of parameter names to fetch (e.g., ['BR', 'BTHETA']).
                        Use browse_parameters to discover available parameters.
            start: Start time in ISO 8601 format (e.g., '2024-01-01').
            stop: End time in ISO 8601 format (e.g., '2024-01-07').
            output_dir: Directory for the output file. Must be provided.
            format: Output file format — 'csv' (default) or 'json'.
        """
        # Call the library function — returns dict keyed by parameter
        lib_result = _fetch_data(
            dataset_id=dataset_id,
            parameters=parameters,
            start=start,
            stop=stop,
        )

        out_dir = Path(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)

        # Descriptive filename: dataset_YYYYMMDD_YYYYMMDD.format
        start_short = start[:10].replace("-", "")
        stop_short = stop[:10].replace("-", "")

        # Merge all parameter DataFrames and write to file
        frames = []
        param_meta = {}
        for param_id, entry in lib_result.items():
            if "error" in entry:
                param_meta[param_id] = {"status": "error", "message": entry["error"]}
                continue
            df = entry["data"]
            df.columns = [f"{param_id}.{c}" for c in df.columns]
            frames.append(df)
            param_meta[param_id] = {
                "status": "success",
                "units": entry["units"],
                "description": entry["description"],
                "rows": len(df),
                "columns": list(df.columns),
                "stats": entry["stats"],
            }

        if not frames:
            return json.dumps({"status": "error", "message": "No data fetched",
                               "parameters": param_meta}, indent=2)

        merged = frames[0]
        for f in frames[1:]:
            merged = merged.join(f, how="outer")

        # Write to file — increment suffix if name already exists
        base_name = f"{dataset_id}_{start_short}_{stop_short}"
        file_path = out_dir / f"{base_name}.{format}"
        counter = 1
        while file_path.exists():
            file_path = out_dir / f"{base_name}_{counter}.{format}"
            counter += 1

        if format == "json":
            data = {"time": merged.index.strftime("%Y-%m-%dT%H:%M:%S.%f").tolist()}
            for col in merged.columns:
                data[col] = [None if pd.isna(v) else v for v in merged[col].tolist()]
            with open(file_path, "w") as f:
                json.dump(data, f)
        else:
            merged.to_csv(file_path)

        return json.dumps({
            "status": "success",
            "file_path": str(file_path),
            "format": format,
            "dataset_id": dataset_id,
            "time_range": {"start": start, "stop": stop},
            "total_rows": len(merged),
            "parameters": param_meta,
        }, indent=2, default=str)

    @mcp.tool()
    def manage_cache(
        action: str,
        category: str = "all",
        mission: str | None = None,
        dataset_ids: list[str] | None = None,
        older_than_days: int | None = None,
        dry_run: bool = True,
        detail: bool = False,
    ) -> str:
        """Manage the local PDS cache — view status, clean files, refresh metadata, or rebuild catalogs.

        Actions:
        - "status": Show disk usage for metadata and data caches. Set detail=True for per-subdirectory breakdown.
        - "clean": Delete cached files. Defaults to dry_run=True (preview only). Filter by category, mission, or age.
        - "refresh_metadata": Re-download PDS label metadata. Specify dataset_ids or mission to scope.
        - "refresh_time_ranges": Update start/stop dates in mission catalog JSONs from Metadex API. Optionally filter by mission.
        - "rebuild_catalog": Regenerate mission catalog JSONs from Metadex API. Optionally filter by mission.

        Args:
            action: One of "status", "clean", "refresh_metadata", "refresh_time_ranges", "rebuild_catalog".
            category: For "clean" — "metadata", "data_cache", or "all" (default).
            mission: Filter to a single mission stem (e.g., "juno", "cassini").
            dataset_ids: For "refresh_metadata" — specific dataset IDs to refresh.
            older_than_days: For "clean" — only delete files older than N days.
            dry_run: For "clean" — if True (default), preview without deleting.
            detail: For "status" — if True, include per-subdirectory breakdown.
        """
        from pdsmcp.cache import (
            cache_status,
            cache_clean,
            refresh_metadata,
            refresh_time_ranges,
            rebuild_catalog,
        )

        if action == "status":
            return json.dumps(cache_status(detail=detail), indent=2)
        elif action == "clean":
            missions_list = [mission] if mission else None
            return json.dumps(
                cache_clean(
                    category=category,
                    missions=missions_list,
                    older_than_days=older_than_days,
                    dry_run=dry_run,
                ),
                indent=2,
            )
        elif action == "refresh_metadata":
            return json.dumps(
                refresh_metadata(dataset_ids=dataset_ids, mission=mission),
                indent=2,
            )
        elif action == "refresh_time_ranges":
            return json.dumps(
                refresh_time_ranges(mission=mission),
                indent=2,
            )
        elif action == "rebuild_catalog":
            return json.dumps(
                rebuild_catalog(mission=mission),
                indent=2,
            )
        else:
            return json.dumps({
                "status": "error",
                "message": f"Unknown action: {action}. "
                           "Valid: status, clean, refresh_metadata, refresh_time_ranges, rebuild_catalog",
            })

    return mcp


def serve():
    """Run the MCP server (stdio transport)."""
    import argparse

    parser = argparse.ArgumentParser(description="PDS PPI MCP server")
    parser.add_argument(
        "--cache-dir", type=str, default=None,
        help="Root directory for all caches (default: ~/.pdsmcp/)",
    )
    args = parser.parse_args()

    if args.cache_dir:
        from pdsmcp.config import configure
        configure(cache_dir=args.cache_dir)

    logging.basicConfig(level=logging.INFO)
    server = create_server()
    server.run()
