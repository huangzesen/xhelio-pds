# CLAUDE.md — xhelio-pds

## Project Overview

`xhelio-pds` (PyPI name) / `pdsmcp` (Python import) is a standalone package that provides NASA PDS Planetary Plasma Interactions (PPI) data access. Works as a Python library (returns DataFrames) or an MCP server (writes files, returns metadata + stats). Part of the xhelio ecosystem, sibling to `cdawebmcp`.

## Key Design Decisions

1. **Library-first, MCP optional.** Core library (`pandas`, `requests`) has no MCP dependency. MCP server requires `pip install xhelio-pds[mcp]`.
2. **`fetch_data` returns DataFrame + stats directly** (library API). The MCP server wrapper writes to temp files and returns metadata + stats only.
3. **One MCP server serves all 17 PDS PPI missions.** `browse_missions` is the discovery entry point.
4. **Single parameter per fetch.** PDS fetch takes one `parameter_id` at a time (unlike cdawebmcp which accepts a list).
5. **PDS3 label parser included.** Regex-based ODL parser (`label_parser.py`), no external XML dependency.

## Tech Stack

- Python 3.10+
- `mcp` (FastMCP) — MCP server SDK (optional)
- `pandas` / `numpy` — DataFrame operations
- `requests` — HTTP client for PDS archive + Metadex API
- No `cdflib` — PDS uses ASCII tables, not CDF

## Commands

```bash
# Install library only
pip install -e .

# Install with MCP server
pip install -e ".[mcp]"

# Install with dev tools
pip install -e ".[dev]"

# Run tests
python -m pytest tests/ -v

# Run the MCP server
python -m pdsmcp

# Build/rebuild mission catalog from Metadex API
python -m pdsmcp.scripts.build_catalog
```

## Package Structure

```
src/pdsmcp/
    __init__.py          # Package entry point
    __main__.py          # python -m pdsmcp
    server.py            # MCP server (FastMCP) — 4 tools
    catalog.py           # Mission JSON loading + browse_missions
    prompts.py           # Prompt assembly for load_mission
    metadata.py          # Parameter metadata (label parsing + cache)
    fetch.py             # PDS data download + DataFrame conversion (largest module)
    label_parser.py      # PDS3 ODL label parser (regex-based)
    http.py              # HTTP client with retry logic
    data/
        missions/        # Bundled mission JSONs (17 missions)
        prompts/         # Prompt templates (generic_role.md, pds_role.md)
    scripts/
        build_catalog.py # Metadex Solr API → mission JSONs
```

## Reference: xhelio Source Files

These xhelio modules contain the code to extract from (DO NOT depend on xhelio — extract and adapt):

| xhelio file | Purpose | Maps to |
|---|---|---|
| `data_ops/fetch_ppi_archive.py` (1627 lines) | PDS data fetch pipeline | `fetch.py` |
| `data_ops/pds3_label_parser.py` (238 lines) | PDS3 ODL label parser | `label_parser.py` |
| `data_ops/http_utils.py` | HTTP retry logic | `http.py` |
| `knowledge/metadex_client.py` | Metadex Solr API client | `scripts/build_catalog.py` |
| `knowledge/mission_prefixes.py` (PPI entries) | Dataset → mission mapping | `catalog.py` |
| `knowledge/prompts/envoy/generic_role.md` | Generic envoy role | `data/prompts/generic_role.md` |
| `knowledge/prompts/envoy_ppi/role.md` | PDS-specific role | `data/prompts/pds_role.md` |
| `scripts/generate_ppi_missions.py` | Catalog generation wrapper | `scripts/build_catalog.py` |
| `knowledge/bootstrap.py` (PPI parts) | Mission JSON population | `scripts/build_catalog.py` |

The xhelio repo is at `../xhelio/` (master branch) if you need to read source files.

## Key Differences from cdawebmcp

- **No cdflib** — PDS uses fixed-width ASCII tables, parsed with custom regex-based label parser
- **Apache directory listing** — no REST API for file discovery, must parse HTML directory indexes
- **Complex directory patterns** — year, orbit, sol, frequency, flat, nested organizations
- **Single parameter per fetch** — `fetch_data(dataset_id, parameter_id, ...)` not `parameters: list`
- **PDS3 + PDS4** — two label formats (ODL regex vs XML)
- **Metadex Solr** for catalog generation, not CDAWeb REST API
