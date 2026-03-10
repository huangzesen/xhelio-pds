# xhelio-pds

NASA PDS Planetary Plasma Interactions data access — browse missions, inspect parameters, fetch PDS data.

Works as a standalone Python library or as an MCP server for any MCP-compatible LLM client (Claude Desktop, Cursor, custom agents).

## What's included

- **17 mission catalogs** with 1200+ datasets — Juno, Cassini, Voyager 1/2, MAVEN, Galileo, New Horizons, and more
- **PDS3 + PDS4 support** — fixed-width ASCII tables with ODL (regex) and XML label parsing
- **Automatic schema validation** — labels are compared across files within each dataset to detect schema drift (field changes, unit changes, missing columns)
- **Structured system prompts** per mission — give an LLM full context about available instruments, datasets, and time coverage

## Installation

```bash
# Library only
pip install xhelio-pds

# With MCP server
pip install xhelio-pds[mcp]
```

## MCP Server

### Configuration (Claude Desktop, Cursor, etc.)

```json
{
  "mcpServers": {
    "pds": {
      "command": "xhelio-pds-mcp"
    }
  }
}
```

With custom cache directory:

```json
{
  "mcpServers": {
    "pds": {
      "command": "xhelio-pds-mcp",
      "args": ["--cache-dir", "/path/to/cache"]
    }
  }
}
```

Or run directly:

```bash
xhelio-pds-mcp
xhelio-pds-mcp --cache-dir /path/to/cache
python -m pdsmcp
```

### Cache directory

All runtime data is stored under a single root directory. Defaults to `~/.pdsmcp/`.

Configure via `--cache-dir` (MCP server) or `pdsmcp.configure()` (library):

```python
import pdsmcp
pdsmcp.configure(cache_dir="/path/to/cache")
```

```
~/.pdsmcp/                     # or custom path via configure()
├── metadata/                  # PDS label-derived parameter metadata
├── data_cache/                # Downloaded PDS data + label files (permanent, reused across fetches)
│   └── jno/fgm/               #   organized by mission/instrument path
│       ├── FGM_JNO_L3_2024001SE_V01.STS
│       └── FGM_JNO_L3_2024001SE_V01.LBL
└── validation/                # Schema consistency records (append-only)
    └── pds3_JNO-J-3-FGM-CAL-V1.0_DATA.json
```

- **`metadata/`** — Parameter metadata parsed from PDS labels. Built lazily on first access per dataset.
- **`data_cache/`** — Permanent cache of downloaded PDS data and label files. Once downloaded, never re-downloaded. Use `manage_cache(action="clean", category="data_cache")` to free disk space.
- **`validation/`** — Schema drift records from comparing labels across files within a dataset. Append-only, one JSON per dataset.

### Tools

| Tool | Description |
|------|-------------|
| `browse_missions()` | List all 17 PDS PPI missions with descriptions, dataset counts, and instruments |
| `load_mission(mission_id)` | Get the complete system prompt for a mission (role instructions + full dataset catalog) |
| `browse_parameters(dataset_id)` | Browse all variables in a dataset — name, type, units, description, plus schema validation summary |
| `fetch_data(dataset_id, parameters, start, stop, output_dir)` | Download PDS data, write to file, return metadata + per-column stats (min, max, mean, std, nan_ratio) |
| `manage_cache(action, ...)` | Cache management — status, clean, refresh metadata, refresh time ranges, rebuild catalog |

### Typical workflow

```
browse_missions  →  load_mission("juno")  →  browse_parameters("pds3:JNO-J-3-FGM-CAL-V1.0:DATA")  →  fetch_data(...)
```

1. Discover available missions
2. Load a mission's full catalog and instructions
3. Inspect dataset parameters to choose what to fetch
4. Fetch data for a time range — returns file path + statistics

## Python Library

```python
from pdsmcp.catalog import browse_missions
from pdsmcp.prompts import build_mission_prompt
from pdsmcp.metadata import browse_parameters
from pdsmcp.fetch import fetch_data

# List all 17 PDS PPI missions
missions = browse_missions()

# Get mission-specific system prompt
prompt = build_mission_prompt("juno")

# Browse dataset parameters (fetches label on first access, cached after)
params = browse_parameters(dataset_id="pds3:JNO-J-3-FGM-CAL-V1.0:DATA")

# Fetch data — returns DataFrames directly
result = fetch_data(
    "pds3:JNO-J-3-FGM-CAL-V1.0:DATA",
    ["BX PLANETOCENTRIC", "BY PLANETOCENTRIC"],
    "2024-01-01", "2024-01-02",
)
bx = result["BX PLANETOCENTRIC"]
print(bx["data"])       # pandas DataFrame
print(bx["units"])      # "NT"
print(bx["stats"])      # per-column {min, max, mean, std, nan_ratio}
```

## Schema validation

When `fetch_data` downloads PDS files, it automatically compares each file's label against the reference schema (captured from the first file seen). Discrepancies are recorded in `~/.pdsmcp/validation/` and surfaced through `browse_parameters`:

- **Missing fields** — present in the reference label but absent from a later file
- **New fields** — present in a later file but not in the reference label
- **Metadata drift** — same field name but different units, type, or size across files

This validation runs on every file during fetch (deduplicated by URL) and builds an append-only archive with full provenance.

Batch validation without fetching full data:

```bash
python -m pdsmcp.scripts.validate_schema --mission juno
python -m pdsmcp.scripts.validate_schema --dataset-id "pds3:JNO-J-3-FGM-CAL-V1.0:DATA" --sample 20
```

## Bundled data

| Data | Count | Description |
|------|-------|-------------|
| Mission catalogs | 17 | Instruments, datasets, time coverage |
| Prompt templates | 2 | Generic role + PDS-specific workflow instructions |

All bundled data ships with the package. No network access needed for browsing — only `fetch_data` and `browse_parameters` (first access) require a connection to PDS.

## Catalog updates

Rebuild from PDS PPI Metadex API:

```bash
# Rebuild mission catalogs
python -m pdsmcp.scripts.build_catalog
python -m pdsmcp.scripts.build_catalog --mission juno
python -m pdsmcp.scripts.build_catalog --list
```

## Development

```bash
pip install -e ".[dev]"
pytest tests/ -v
```

## License

MIT
