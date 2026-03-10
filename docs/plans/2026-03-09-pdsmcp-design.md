# Design: pdsmcp — Standalone PDS PPI MCP Server

**Date:** 2026-03-09
**Status:** Draft

## Overview

`pdsmcp` is a standalone Python package that provides access to NASA's PDS Planetary Plasma Interactions (PPI) archive. Works as a Python library (returns DataFrames) or as an MCP server (writes files, returns metadata + stats). Part of the xhelio ecosystem, sibling to `cdawebmcp`.

**PyPI name:** `xhelio-pds`
**Python import:** `pdsmcp`

## Motivation

Same as cdawebmcp — extract PDS data access out of xhelio into a standalone package. Makes PDS PPI data accessible to any MCP-compatible agent while shrinking xhelio's codebase. The PDS fetch pipeline is 1627 lines + a 238-line label parser — significant code that is general-purpose.

## MCP Tools

Four tools, same discovery funnel as cdawebmcp:

### 1. `browse_missions()`

**Purpose:** Entry point — list all available PDS PPI missions.

**Parameters:** None (or optional `query: str` for keyword filtering).

**Returns:** Array of `{id, name, description, dataset_count, instruments: [str]}` — from bundled mission catalog JSONs.

**Source data:** 17 missions, ~1200+ datasets, aggregated from PDS PPI Metadex Solr API.

### 2. `load_mission(mission_id: str)`

**Purpose:** Returns the complete system prompt for a PDS PPI mission.

**Parameters:**
- `mission_id` (required): Mission identifier (e.g., `"juno"`, `"cassini"`, `"voyager1"`)

**Returns:** Assembled prompt string:
1. Generic envoy role instructions
2. PDS-specific instructions (PDS3/PDS4 conventions, fetch patterns, time formats)
3. Full mission dataset catalog as markdown

### 3. `browse_parameters(dataset_id: str)`

**Purpose:** Returns variable-level metadata for a PDS dataset.

**Parameters:**
- `dataset_id` (required): PDS dataset ID — PDS4 URN (e.g., `"urn:nasa:pds:cassini-mag-cal:data-1sec-krtp"`) or PDS3 (e.g., `"pds3:JNO-J-3-FGM-CAL-V1.0:DATA"`)
- `dataset_ids` (optional): Array for batch lookup

**Returns:** Per-dataset parameter list with `{name, type, units, description, size, fill}`. Metadata extracted from PDS label files (PDS3 ODL or PDS4 XML), cached locally.

### 4. `fetch_data(dataset_id, parameter_id, start, stop)`

**Purpose:** Downloads PDS data and returns it.

This tool has **two interfaces**:

- **Python library** (`from pdsmcp.fetch import fetch_data`): Returns a dict with DataFrames directly — `{data: DataFrame, units, description, stats}`. This is what xhelio uses.
- **MCP server** (`server.py` wrapper): Writes data to a temp file, returns metadata + file path + rich stats (min, max, mean, std, nan_ratio per column).

#### Library API

**Parameters:**
- `dataset_id` (required): PDS dataset ID (URN or pds3:)
- `parameter_id` (required): Parameter name (e.g., `"BR"`, `"BX PLANETOCENTRIC"`)
- `start` (required): Start datetime (ISO 8601)
- `stop` (required): End datetime (ISO 8601)

**Returns:** Dict with `{data: DataFrame, units, description, stats}`.

#### MCP Tool API

**Additional parameters:**
- `format` (optional): `"csv"` (default) or `"json"`
- `output_dir` (optional): Directory for output file

**Returns:** Rich metadata + file path + stats. No inline data.

**Why two interfaces:** xhelio imports the library and gets DataFrames directly. MCP consumers get file paths + stats for LLM decision-making.

## Key Differences from cdawebmcp

| Aspect | cdawebmcp | pdsmcp |
|--------|-----------|--------|
| **Data format** | CDF (binary) | PDS fixed-width ASCII (.TAB, .sts) |
| **API** | CDAWeb REST API (JSON) | PDS PPI archive (Apache directory listing + Metadex Solr) |
| **Labels** | CDF ISTP metadata | PDS3 ODL (.lbl) or PDS4 XML |
| **Parsing** | `cdflib` | Custom regex-based parser |
| **Dataset IDs** | CDAWeb IDs (`AC_H2_MFI`) | PDS URNs / pds3: prefixes |
| **Fetch parameter** | `parameters: list[str]` (multiple) | `parameter_id: str` (single) |
| **Directory structure** | Flat file list from REST API | Complex patterns (year, orbit, sol, freq, nested) |
| **Catalog source** | CDAWeb REST API `/datasets` | PDS PPI Metadex Solr API |

## Package Structure

```
pdsmcp/
├── pyproject.toml
├── README.md
├── src/
│   └── pdsmcp/
│       ├── __init__.py
│       ├── __main__.py            # python -m pdsmcp entry point
│       ├── server.py              # MCP server (FastMCP)
│       ├── catalog.py             # Mission catalog loading + aggregation
│       ├── metadata.py            # Parameter metadata (label parsing + cache)
│       ├── fetch.py               # PDS data download + DataFrame conversion
│       ├── label_parser.py        # PDS3 ODL label parser (regex-based)
│       ├── http.py                # HTTP client with retry logic
│       ├── prompts.py             # Prompt assembly for load_mission
│       ├── data/
│       │   ├── missions/          # Bundled mission JSONs (17 missions)
│       │   │   ├── cassini.json
│       │   │   ├── juno.json
│       │   │   └── ...
│       │   └── prompts/
│       │       ├── generic_role.md
│       │       └── pds_role.md
│       └── scripts/
│           └── build_catalog.py   # Metadex Solr API → mission JSONs
├── tests/
│   ├── test_catalog.py
│   ├── test_metadata.py
│   ├── test_fetch.py
│   ├── test_label_parser.py
│   └── test_server.py
└── .github/
    └── workflows/
        └── update-catalog.yml     # Monthly CI job to rebuild mission JSONs
```

## Dependencies

```toml
[project]
dependencies = [
    "pandas>=2.0",
    "numpy>=1.24",
    "requests>=2.28",
]

[project.optional-dependencies]
mcp = ["mcp>=1.0"]
dev = ["pytest", "pytest-cov"]
```

Note: No `cdflib` dependency — PDS uses ASCII tables, not CDF.

## Data Flow

```
User/LLM
  │
  ├── browse_missions()
  │     └── reads bundled data/missions/*.json → returns mission list
  │
  ├── load_mission("juno")
  │     ├── reads data/prompts/generic_role.md
  │     ├── reads data/prompts/pds_role.md
  │     ├── reads data/missions/juno.json → renders as markdown
  │     └── returns assembled system prompt
  │
  ├── browse_parameters("pds3:JNO-J-3-FGM-CAL-V1.0:DATA")
  │     ├── checks ~/.pdsmcp/metadata/{safe_id}.json (local cache)
  │     ├── if miss → downloads a label file from PDS archive, parses, caches
  │     └── returns parameter list
  │
  └── fetch_data("pds3:JNO-J-3-FGM-CAL-V1.0:DATA", "BX PLANETOCENTRIC", "2024-01-01", "2024-01-07")
        ├── resolves collection URL from dataset ID
        ├── discovers data files (Apache directory listing, time filtering)
        ├── downloads data + label file pairs
        ├── parses PDS3 labels (regex) or PDS4 labels (XML)
        ├── reads fixed-width ASCII tables
        ├── concatenates, deduplicates, time-trims
        ├── cleans fill values → NaN
        └── returns DataFrame with stats (library) or writes file + returns metadata (MCP)
```

### xhelio Integration Flow

When used from xhelio, the envoy LLM controls the workflow:

```
Envoy LLM
  │
  ├── browse_parameters("pds3:JNO-J-3-FGM-CAL-V1.0:DATA")  → parameter metadata
  ├── fetch_data(dataset_id, "BX PLANETOCENTRIC", ...)       → {data: DataFrame, stats}
  │     ↓
  │   LLM inspects stats — nan_ratio, row count, ranges
  │     ↓
  ├── if good → stores in DataStore directly (library import, no load_file needed)
  │   if bad  → try different dataset or inform user
  └── [orchestrator cycle-end cleanup]
```

## Catalog Build Pipeline

Mission JSONs are generated by `scripts/build_catalog.py` (extracted from xhelio's `generate_ppi_missions.py` + `metadex_client.py`):

1. Queries PDS PPI Metadex Solr API (`https://pds-ppi.igpp.ucla.edu/metadex/collection/select/`)
2. Groups ~1200 collections by mission using prefix matching
3. Categorizes into instrument groups
4. Writes one JSON per mission with: id, name, profile, instruments, datasets (description, dates, slot, archive_type)

Each dataset entry includes `slot` (archive path) and `archive_type` (3=PDS3, 4=PDS4) — needed for fetch URL resolution.

## Cache Layout

```
~/.pdsmcp/
├── metadata/           # Parameter metadata cache (auto-populated from labels)
│   ├── pds3_JNO-J-3-FGM-CAL-V1.0_DATA.json
│   └── urn_nasa_pds_cassini-mag-cal_data-1sec-krtp.json
└── data_cache/         # Downloaded data files (optional, configurable)
    └── ...
```

Cache location configurable via `PDSMCP_CACHE_DIR` env var.

## Usage

### As MCP server (stdio transport)

```json
{
  "mcpServers": {
    "pds": {
      "command": "python",
      "args": ["-m", "pdsmcp"]
    }
  }
}
```

### As Python library (what xhelio uses)

```python
from pdsmcp.catalog import browse_missions
from pdsmcp.prompts import build_mission_prompt
from pdsmcp.metadata import browse_parameters
from pdsmcp.fetch import fetch_data

missions = browse_missions()
prompt = build_mission_prompt("juno")
params = browse_parameters("pds3:JNO-J-3-FGM-CAL-V1.0:DATA")
result = fetch_data("pds3:JNO-J-3-FGM-CAL-V1.0:DATA", "BX PLANETOCENTRIC", "2024-01-01", "2024-01-07")
# result["data"] → pandas DataFrame
# result["units"] → "nT"
# result["stats"] → {"1": {"min": ..., "max": ..., "nan_ratio": ...}}
```

## Integration with xhelio

### Migration Plan

After `pdsmcp` is published:

1. xhelio adds `pdsmcp` (or `xhelio-pds`) as a dependency (direct Python import)
2. `knowledge/envoys/ppi/handlers.py` — handlers become thin wrappers:
   - `handle_browse_parameters` → calls `pdsmcp.metadata.browse_parameters()`
   - `handle_fetch_data_ppi` → calls `pdsmcp.fetch.fetch_data()`, gets DataFrame directly, stores in DataStore
3. The internal `data_ops/fetch_ppi_archive.py`, `data_ops/pds3_label_parser.py`, `knowledge/metadex_client.py`, PPI mission JSONs, PPI prompt templates all move out of xhelio
4. No MCP client needed — direct Python imports

## What Gets Extracted from xhelio

| xhelio module | → pdsmcp module | Notes |
|---|---|---|
| `data_ops/fetch_ppi_archive.py` (1627 lines) | `fetch.py` | Core fetch pipeline, remove event bus dependency |
| `data_ops/pds3_label_parser.py` (238 lines) | `label_parser.py` | Pure regex, minimal changes |
| `knowledge/envoys/ppi/*.json` | `data/missions/*.json` | Regenerated by build script |
| `knowledge/prompts/envoy_ppi/role.md` | `data/prompts/pds_role.md` | Adapted (remove xhelio refs) |
| `knowledge/prompts/envoy/generic_role.md` | `data/prompts/generic_role.md` | Adapted |
| `knowledge/metadex_client.py` | `scripts/build_catalog.py` | Metadex Solr client |
| `knowledge/mission_prefixes.py` (PPI entries) | `catalog.py` | PDS prefix matching |
| `data_ops/http_utils.py` | `http.py` | Retry logic |
| `scripts/generate_ppi_missions.py` | `scripts/build_catalog.py` | Catalog generation |

## Resolved Decisions

1. **Repository location:** Separate repo — `huangzesen/pdsmcp`
2. **PyPI name:** `xhelio-pds` (import as `pdsmcp`)
3. **Library-first, MCP optional.** Core library has no MCP dependency. MCP via `pip install xhelio-pds[mcp]`.
4. **fetch_data returns DataFrame + stats directly** (library API). MCP wrapper handles file-writing.
5. **Single parameter per fetch.** Unlike cdawebmcp (multiple parameters), PDS fetch takes one `parameter_id` at a time — matches the existing xhelio API.
6. **PDS3 label parser included.** The regex-based ODL parser moves into the package as `label_parser.py`.
7. **Metadex Solr API for catalog.** Not the newer PDS API — Metadex covers PPI specifically and is what xhelio already uses.
