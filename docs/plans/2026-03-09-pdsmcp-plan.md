# pdsmcp Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Create `pdsmcp`, a standalone Python package that exposes NASA PDS PPI data as a library (DataFrames) and MCP server (4 tools: `browse_missions`, `load_mission`, `browse_parameters`, `fetch_data`).

**Architecture:** Library-first (`fetch_data` returns DataFrames + stats), MCP optional (`server.py` wraps for file output). Extracted from xhelio's `data_ops/fetch_ppi_archive.py` (1627 lines), `data_ops/pds3_label_parser.py` (238 lines), and `knowledge/metadex_client.py`.

**Tech Stack:** Python 3.10+, `mcp` (optional), `pandas`, `numpy`, `requests`

**Design doc:** `docs/plans/2026-03-09-pdsmcp-design.md`

**Reference codebase:** `../xhelio/` (master branch) contains all source files to extract from. Read them directly — do NOT depend on xhelio.

---

## Task 1: Repository scaffold and package structure

**Files:**
- Create: `pyproject.toml`
- Create: `README.md` (placeholder)
- Create: `src/pdsmcp/__init__.py`
- Create: `src/pdsmcp/__main__.py`
- Create: `src/pdsmcp/py.typed`

**Step 1: Create pyproject.toml**

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "xhelio-pds"
version = "0.1.0"
description = "PDS PPI data access for heliophysics — Python library and MCP server"
readme = "README.md"
license = "MIT"
requires-python = ">=3.10"
authors = [
    { name = "Zesen Huang" },
]
keywords = ["mcp", "pds", "ppi", "nasa", "heliophysics", "planetary"]
classifiers = [
    "Development Status :: 3 - Alpha",
    "Intended Audience :: Science/Research",
    "Topic :: Scientific/Engineering :: Astronomy",
    "License :: OSI Approved :: MIT License",
    "Programming Language :: Python :: 3",
]
dependencies = [
    "pandas>=2.0",
    "numpy>=1.24",
    "requests>=2.28",
]

[project.optional-dependencies]
mcp = ["mcp>=1.0"]
dev = ["pytest", "pytest-cov"]

[project.scripts]
xhelio-pds-mcp = "pdsmcp:main"

[tool.hatch.build.targets.wheel]
packages = ["src/pdsmcp"]

[tool.pytest.ini_options]
testpaths = ["tests"]
```

**Step 2: Create package __init__.py**

```python
"""pdsmcp — PDS PPI data access library and MCP server."""

__version__ = "0.1.0"


def main():
    """Entry point for the MCP server CLI."""
    from pdsmcp.server import serve
    serve()
```

**Step 3: Create __main__.py**

```python
"""Allow running as `python -m pdsmcp`."""
from pdsmcp import main

main()
```

**Step 4: Create py.typed marker** (empty file)

**Step 5: Commit**

```bash
git add -A
git commit -m "chore: scaffold pdsmcp package"
```

---

## Task 2: HTTP utilities

**Files:**
- Create: `src/pdsmcp/http.py`
- Create: `tests/test_http.py`

This is identical to cdawebmcp's http.py — extract from xhelio's `data_ops/http_utils.py`, removing event bus dependency.

**Step 1: Write the test**

```python
"""Tests for HTTP retry logic."""
import pytest
from unittest.mock import patch, MagicMock
from requests.exceptions import Timeout, ConnectionError as ReqConnectionError

from pdsmcp.http import request_with_retry


def test_request_success():
    with patch("pdsmcp.http.requests.get") as mock_get:
        mock_get.return_value = MagicMock(status_code=200)
        resp = request_with_retry("https://example.com")
        assert resp.status_code == 200


def test_request_retry_on_timeout():
    with patch("pdsmcp.http.requests.get") as mock_get:
        mock_get.side_effect = [Timeout(), MagicMock(status_code=200)]
        resp = request_with_retry("https://example.com", retries=2, backoff=0)
        assert resp.status_code == 200
        assert mock_get.call_count == 2


def test_request_raises_after_retries():
    with patch("pdsmcp.http.requests.get") as mock_get:
        mock_get.side_effect = Timeout()
        with pytest.raises(Timeout):
            request_with_retry("https://example.com", retries=2, backoff=0)
        assert mock_get.call_count == 2
```

**Step 2: Write implementation** — copy from cdawebmcp's `http.py` (they're identical). Read `../cdawebmcp/src/cdawebmcp/http.py`.

**Step 3: Run tests, commit**

---

## Task 3: PDS3 label parser

**Files:**
- Create: `src/pdsmcp/label_parser.py`
- Create: `tests/test_label_parser.py`

Extract from xhelio's `data_ops/pds3_label_parser.py` (238 lines). This is a pure-regex parser with no external dependencies — copy nearly verbatim.

**Step 1: Write the test**

```python
"""Tests for PDS3 ODL label parser."""
import pytest
from pdsmcp.label_parser import parse_pds3_label


SAMPLE_LABEL = '''
PDS_VERSION_ID = PDS3
^TABLE = ("data.sts", 8758<BYTES>)
OBJECT = TABLE
  ROWS = 100
  ROW_BYTES = 80
  OBJECT = COLUMN
    COLUMN_NUMBER = 1
    NAME = "SAMPLE UTC"
    START_BYTE = 1
    BYTES = 24
    DATA_TYPE = TIME
    UNIT = "N/A"
    DESCRIPTION = "Sample time"
  END_OBJECT = COLUMN
  OBJECT = COLUMN
    COLUMN_NUMBER = 2
    NAME = "BX PLANETOCENTRIC"
    START_BYTE = 25
    BYTES = 14
    DATA_TYPE = ASCII_REAL
    UNIT = "nT"
    DESCRIPTION = "Magnetic field X component"
    NULL_CONSTANT = -9999.999
  END_OBJECT = COLUMN
END_OBJECT = TABLE
END
'''


def test_parse_basic_label():
    result = parse_pds3_label(SAMPLE_LABEL)
    assert result["table_type"] == "fixed_width"
    assert result["records"] == 100
    assert result["row_bytes"] == 80
    assert result["header_bytes"] == 8757  # 8758 - 1 (0-based)
    assert len(result["fields"]) == 2


def test_parse_field_metadata():
    result = parse_pds3_label(SAMPLE_LABEL)
    bx = result["fields"][1]
    assert bx["name"] == "BX PLANETOCENTRIC"
    assert bx["unit"] == "nT"
    assert bx["offset"] == 25
    assert bx["length"] == 14
    assert bx["null_constant"] == "-9999.999"


def test_no_table_raises():
    with pytest.raises(ValueError, match="No OBJECT = TABLE"):
        parse_pds3_label("PDS_VERSION_ID = PDS3\nEND\n")
```

**Step 2: Copy implementation** from `../xhelio/data_ops/pds3_label_parser.py` (238 lines). No changes needed — it's already dependency-free.

**Step 3: Run tests, commit**

```bash
git commit -m "feat: add PDS3 ODL label parser"
```

---

## Task 4: Mission catalog module

**Files:**
- Create: `src/pdsmcp/catalog.py`
- Create: `tests/test_catalog.py`
- Create: `src/pdsmcp/data/missions/` (directory, populated later in Task 8)

Same pattern as cdawebmcp — loads bundled mission JSONs, provides `browse_missions()` and `mission_to_markdown()`.

**Step 1: Write the test** — mirror cdawebmcp's test_catalog.py but with PDS-style mission data:

```python
"""Tests for PDS mission catalog loading."""
import json
import pytest
from pathlib import Path
from unittest.mock import patch

from pdsmcp.catalog import (
    load_mission_json,
    browse_missions,
    mission_to_markdown,
)


@pytest.fixture
def sample_mission(tmp_path):
    mission = {
        "id": "JUNO_PPI",
        "name": "Juno (PDS PPI)",
        "profile": {"description": "Juno data from PDS PPI archive."},
        "instruments": {
            "FGM": {
                "name": "FGM",
                "keywords": ["magnetic"],
                "datasets": {
                    "pds3:JNO-J-3-FGM-CAL-V1.0:DATA": {
                        "description": "JUNO FGM CALIBRATED DATA",
                        "start_date": "2016-07-04",
                        "stop_date": "2025-01-01",
                        "slot": "/data/JNO-J-3-FGM-CAL-V1.0/DATA",
                        "archive_type": 3,
                    }
                },
            }
        },
    }
    (tmp_path / "juno.json").write_text(json.dumps(mission))
    return tmp_path, mission


def test_load_mission_json(sample_mission):
    missions_dir, _ = sample_mission
    with patch("pdsmcp.catalog.get_missions_dir", return_value=missions_dir):
        result = load_mission_json("juno")
    assert result["id"] == "JUNO_PPI"


def test_browse_missions(sample_mission):
    missions_dir, _ = sample_mission
    with patch("pdsmcp.catalog.get_missions_dir", return_value=missions_dir):
        result = browse_missions()
    assert len(result) == 1
    assert result[0]["id"] == "JUNO_PPI"
    assert result[0]["dataset_count"] == 1


def test_mission_to_markdown(sample_mission):
    missions_dir, _ = sample_mission
    with patch("pdsmcp.catalog.get_missions_dir", return_value=missions_dir):
        mission = load_mission_json("juno")
    md = mission_to_markdown(mission)
    assert "Dataset Catalog" in md
    assert "pds3:JNO-J-3-FGM-CAL-V1.0:DATA" in md
```

**Step 2: Write implementation** — read `../cdawebmcp/src/cdawebmcp/catalog.py` and adapt. The structure is the same, just different data shape (PDS datasets have `slot` and `archive_type` fields).

**Step 3: Run tests, commit**

---

## Task 5: Prompt assembly module

**Files:**
- Create: `src/pdsmcp/prompts.py`
- Create: `src/pdsmcp/data/prompts/generic_role.md`
- Create: `src/pdsmcp/data/prompts/pds_role.md`
- Create: `tests/test_prompts.py`

**Step 1: Create prompt templates**

`data/prompts/generic_role.md` — adapt from xhelio's `knowledge/prompts/envoy/generic_role.md`, remove xhelio-specific references:

```markdown
You are a PDS data specialist — an expert in NASA's Planetary Data System archive.

## Your Role

- You know your mission's instruments, datasets, and data access methods.
- Your dataset catalog is embedded below — use it to identify the right datasets for user requests.
- Use `browse_parameters` to inspect dataset variables before fetching.
- Use `fetch_data` to download data.
```

`data/prompts/pds_role.md` — adapt from `../xhelio/knowledge/prompts/envoy_ppi/role.md`, removing xhelio-specific tool names (`events`, `manage_session_assets`):

Read `../xhelio/knowledge/prompts/envoy_ppi/role.md` and adapt it. Key changes:
- Remove references to `events(action='check')` and session history
- Remove references to orchestrator delegation
- Keep the dataset selection workflow, time format guidance, data availability validation
- Replace `fetch_data_ppi` with `fetch_data`

**Step 2: Write test** — mirror cdawebmcp's test_prompts.py

**Step 3: Write implementation** — read `../cdawebmcp/src/cdawebmcp/prompts.py` and adapt.

**Step 4: Run tests, commit**

---

## Task 6: Parameter metadata module

**Files:**
- Create: `src/pdsmcp/metadata.py`
- Create: `tests/test_metadata.py`

Extracted from xhelio's `data_ops/fetch_ppi_archive.py` — specifically the metadata caching and label-based metadata extraction functions.

**Step 1: Write the test**

```python
"""Tests for PDS parameter metadata resolution."""
import json
import pytest
from pathlib import Path
from unittest.mock import patch

from pdsmcp.metadata import browse_parameters


def test_browse_parameters_from_cache(tmp_path):
    cache_dir = tmp_path / "metadata"
    cache_dir.mkdir()

    metadata = {
        "parameters": [
            {"name": "SAMPLE UTC", "type": "isotime", "units": "N/A"},
            {"name": "BX PLANETOCENTRIC", "type": "double", "units": "nT",
             "description": "Magnetic field X", "size": [1]},
        ],
        "startDate": "2016-07-04",
        "stopDate": "2025-01-01",
    }
    safe_id = "pds3_JNO-J-3-FGM-CAL-V1.0_DATA"
    (cache_dir / f"{safe_id}.json").write_text(json.dumps(metadata))

    with patch("pdsmcp.metadata.get_cache_dir", return_value=cache_dir):
        result = browse_parameters("pds3:JNO-J-3-FGM-CAL-V1.0:DATA")

    assert result["status"] == "success"
    param_names = [p["name"] for p in result["parameters"]]
    assert "SAMPLE UTC" not in param_names  # time column filtered
    assert "BX PLANETOCENTRIC" in param_names
```

**Step 2: Write implementation**

Key functions to extract from `../xhelio/data_ops/fetch_ppi_archive.py`:
- `_load_cached_metadata()` (line 195) — loads from `~/.pdsmcp/metadata/{safe_id}.json`
- `_find_param_meta_safe()` (line 213)
- `_build_metadata_from_label()` (line 241) — converts label fields to parameter metadata
- `_populate_metadata_from_label()` (line 287) — caches metadata extracted from labels
- `_find_one_label()` (line 323) — finds a label file in a collection for metadata extraction
- `fetch_ppi_label_metadata()` (line 394) — network fallback: downloads a label, parses it, caches

Also need a `dataset_id_to_safe_filename()` helper:
```python
def dataset_id_to_safe_filename(dataset_id: str) -> str:
    """Convert PDS dataset ID to filesystem-safe filename."""
    return dataset_id.replace(":", "_").replace("/", "_")
```

Key differences from cdawebmcp's metadata.py:
- No Master CDF — metadata comes from PDS label files (PDS3 ODL or PDS4 XML)
- Cache uses safe filenames (colons → underscores)
- Time column filtering: skip columns named like "time", "epoch", "utc", etc.

**Step 3: Run tests, commit**

---

## Task 7: Data fetching module (largest task)

**Files:**
- Create: `src/pdsmcp/fetch.py`
- Create: `tests/test_fetch.py`

This is the core of the package — 1627 lines extracted from `../xhelio/data_ops/fetch_ppi_archive.py`. The library API returns DataFrames + stats.

**Step 1: Write the test**

```python
"""Tests for PDS data fetching — unit tests with mocked network."""
import pytest
import pandas as pd
import numpy as np

from pdsmcp.fetch import compute_stats


def test_compute_stats():
    df = pd.DataFrame({"a": [1.0, 2.0, 3.0, np.nan], "b": [4.0, 5.0, 6.0, 7.0]})
    stats = compute_stats(df)
    assert stats["a"]["nan_ratio"] == 0.25
    assert stats["a"]["min"] == 1.0
    assert stats["a"]["max"] == 3.0
    assert stats["b"]["nan_ratio"] == 0.0


def test_compute_stats_all_nan():
    df = pd.DataFrame({"a": [np.nan, np.nan]})
    stats = compute_stats(df)
    assert stats["a"]["nan_ratio"] == 1.0
    assert stats["a"]["min"] is None


def test_parse_html_listing():
    from pdsmcp.fetch import _parse_html_listing
    html = '''
    <html><body><pre>
    <a href="2024/">2024/</a>  2024-06-01 12:00
    <a href="2023/">2023/</a>  2023-12-01 12:00
    <a href="readme.txt">readme.txt</a>  2024-01-01
    </pre></body></html>
    '''
    entries = _parse_html_listing(html)
    dirs = [e for e in entries if e["is_dir"]]
    assert len(dirs) == 2
    assert dirs[0]["name"] == "2024/"


def test_dataset_id_to_safe_filename():
    from pdsmcp.metadata import dataset_id_to_safe_filename
    assert dataset_id_to_safe_filename("pds3:JNO-J-3-FGM:DATA") == "pds3_JNO-J-3-FGM_DATA"
    assert dataset_id_to_safe_filename("urn:nasa:pds:cassini-mag:data") == "urn_nasa_pds_cassini-mag_data"
```

**Step 2: Write implementation**

Extract from `../xhelio/data_ops/fetch_ppi_archive.py`. Read the full file and extract these function groups:

**Public API:**
- `fetch_data()` — new wrapper that calls `fetch_ppi_archive_data()` and adds stats. Returns `{data: DataFrame, units, description, stats}`.
- `compute_stats()` — same as cdawebmcp.

**URL Resolution** (lines 426-563):
- `_resolve_collection_url()`
- `_resolve_pds4_collection_url()`
- `_resolve_pds3_collection_url()`
- `_get_pds3_slot()` — reads slot from mission JSON
- `_match_collection()`

**Directory Listing** (lines 565-610):
- `_list_directory()` — HTTP GET + HTML parsing
- `_parse_html_listing()` — regex parse of Apache directory index

**File Discovery** (lines 598-970):
- `_discover_data_files()` — dispatcher for different organization patterns
- `_discover_year_organized()`
- `_discover_orbit_organized()`
- `_discover_sol_organized()`
- `_discover_freq_organized()`
- `_discover_flat()`
- `_discover_recursive()`
- `_parse_sol_dir_dates()`
- `_filter_pairs_by_filename_time()`
- `_pair_data_and_labels()`

**Download** (lines 1015-1055):
- `_download_file()` — download + local cache

**Label Parsing** (lines 1056-1243):
- `_parse_xml_label()` — PDS4 XML label parser
- `_parse_delimited_label()`
- `_parse_fixed_width_label()`
- `_extract_special_constants()`

**Table Reading** (lines 1244-1380):
- `_read_table()` — dispatcher
- `_read_fixed_width_table()`
- `_read_delimited_table()`

**Parameter Extraction** (lines 1381-1627):
- `_extract_param_df()` — extract one parameter from parsed table
- `_parse_pds_timestamps()` — multiple timestamp format handler
- `_parse_pds3_space_timestamps()` — space-separated date/time format
- `_find_param_columns()` — find matching columns for a parameter name
- `_find_time_column()` — find the time/epoch column
- `_find_param_meta()` — find parameter metadata from info dict

**Key changes when extracting:**
1. Remove all `from agent.event_bus import ...` and `get_event_bus().emit(...)` calls — replace with `logger.info()` / `logger.warning()`
2. Remove `from data_ops.http_utils import ...` — use `from pdsmcp.http import request_with_retry`
3. Remove `from data_ops.pds3_label_parser import ...` — use `from pdsmcp.label_parser import parse_pds3_label`
4. Change `CACHE_DIR` from project-relative to `~/.pdsmcp/data_cache/` (configurable via `PDSMCP_CACHE_DIR` env var)
5. Change `_get_pds3_slot()` to load mission JSONs from the package's bundled data instead of xhelio's knowledge directory
6. Add `compute_stats()` function
7. Wrap `fetch_ppi_archive_data()` in a new `fetch_data()` that adds stats to the result

**Step 3: Run tests, commit**

```bash
git commit -m "feat: add PDS data fetching with library-first API"
```

---

## Task 8: MCP server (FastMCP)

**Files:**
- Create: `src/pdsmcp/server.py`
- Create: `tests/test_server.py`

Same structure as cdawebmcp — 4 tools, `fetch_data` wrapper writes files + returns metadata.

**Step 1: Write the test**

```python
"""Tests for MCP server tool registration."""
import pytest


def test_server_has_four_tools():
    from pdsmcp.server import create_server
    server = create_server()
    tool_names = [t.name for t in server.list_tools()]
    assert "browse_missions" in tool_names
    assert "load_mission" in tool_names
    assert "browse_parameters" in tool_names
    assert "fetch_data" in tool_names
    assert len(tool_names) == 4
```

**Step 2: Write implementation**

Read `../cdawebmcp/src/cdawebmcp/server.py` and adapt:
- Import from `pdsmcp.*` instead of `cdawebmcp.*`
- `fetch_data` MCP wrapper: calls `pdsmcp.fetch.fetch_data()`, gets DataFrame, writes to temp file, returns metadata + stats. Note: PDS fetch takes a single `parameter_id` (not a list), so the wrapper handles one parameter at a time.
- Tool descriptions reference PDS conventions (URNs, pds3: prefixes) instead of CDAWeb.

**Step 3: Run tests, commit**

---

## Task 9: Build catalog script

**Files:**
- Create: `src/pdsmcp/scripts/__init__.py`
- Create: `src/pdsmcp/scripts/build_catalog.py`

Extracted from xhelio's `scripts/generate_ppi_missions.py` + `knowledge/metadex_client.py` + `knowledge/bootstrap.py` (PPI parts) + `knowledge/mission_prefixes.py` (PPI entries).

**Step 1: Write the script**

Key components to extract and combine:

**From `../xhelio/knowledge/metadex_client.py`:**
- `METADEX_BASE` URL
- `_FIELDS` list
- `fetch_all_ppi_collections()` — single HTTP GET to Metadex Solr
- `metadex_id_to_dataset_id()` — PDS3/4 ID normalization
- `_normalize_doc()` — Solr document normalization

**From `../xhelio/knowledge/mission_prefixes.py` (PPI entries only):**
- PDS4 URN prefixes (`urn:nasa:pds:cassini-`, `urn:nasa:pds:juno`, etc.)
- PDS3 prefixes (`pds3:JNO-`, `pds3:CO-`, etc.)
- `match_dataset_to_mission()` — but only the PPI prefix table

**From `../xhelio/knowledge/bootstrap.py`:**
- `populate_ppi_missions()` — builds mission JSONs from Metadex collections
- Mission stem → name mapping
- Instrument grouping logic

Changes:
- Remove all xhelio imports (event bus, config, etc.)
- Use `pdsmcp.http.request_with_retry` for HTTP
- Output to `src/pdsmcp/data/missions/`

**Step 2: Run the script to populate missions**

```bash
python -m pdsmcp.scripts.build_catalog
```

This should generate ~17 mission JSONs in `src/pdsmcp/data/missions/`.

**Step 3: Commit**

```bash
git add src/pdsmcp/scripts/ src/pdsmcp/data/missions/
git commit -m "feat: add catalog build script and populate mission JSONs from Metadex"
```

---

## Task 10: README, LICENSE, and packaging polish

**Files:**
- Create/modify: `README.md`
- Create: `LICENSE` (MIT)
- Modify: `.gitignore` (add `*.cdf`, `ppi_data/`)

Cover in README: what it is, install (`pip install xhelio-pds`), MCP config JSON, Python library usage, the 4 tools with examples, catalog update instructions.

**Commit**

```bash
git commit -m "docs: add README, LICENSE, and packaging polish"
```

---

## Task 11: Integration smoke test

**Step 1: Install and verify**

```bash
pip install -e ".[dev]"
python -m pytest tests/ -v
```

**Step 2: Test Python API**

```python
from pdsmcp.catalog import browse_missions
from pdsmcp.prompts import build_mission_prompt
from pdsmcp.metadata import browse_parameters

# Test 1: Browse missions
missions = browse_missions()
print(f"Found {len(missions)} missions")
assert len(missions) > 0

# Test 2: Load mission prompt
prompt = build_mission_prompt("juno")
assert "pds3:JNO" in prompt
print(f"Juno prompt: {len(prompt)} chars")

# Test 3: Browse parameters (may need network if not cached)
# Skip if no metadata cached — this is populated on first fetch
```

**Step 3: Test MCP server** (optional, requires `pip install -e ".[mcp]"`)

```bash
python -m pdsmcp &
# Use MCP inspector or manual test
```

**Step 4: Create GitHub repo and push**

```bash
gh repo create huangzesen/pdsmcp --public --source=. --push
```

---

## Summary

| Task | What | Key files |
|------|------|-----------|
| 1 | Package scaffold | `pyproject.toml`, `__init__.py`, `__main__.py` |
| 2 | HTTP utilities | `http.py` (copy from cdawebmcp) |
| 3 | PDS3 label parser | `label_parser.py` (238 lines, copy from xhelio) |
| 4 | Mission catalog | `catalog.py` (adapt from cdawebmcp) |
| 5 | Prompt assembly | `prompts.py`, `data/prompts/*.md` |
| 6 | Parameter metadata | `metadata.py` (label-based, not Master CDF) |
| 7 | Data fetching | `fetch.py` (~1600 lines, largest task) |
| 8 | MCP server | `server.py` (4 tools, adapt from cdawebmcp) |
| 9 | Build catalog script | `scripts/build_catalog.py` (Metadex Solr) |
| 10 | README + polish | Docs, LICENSE |
| 11 | Smoke test | End-to-end verification |

## Key Extraction Notes

The biggest risk is Task 7 (fetch.py). The xhelio source (`data_ops/fetch_ppi_archive.py`) has 40+ functions and 1627 lines. When extracting:

1. **Read the entire file first** — understand the call graph before copying
2. **Remove event bus** — replace `get_event_bus().emit(...)` with `logger.info()` / `logger.debug()`
3. **Fix imports** — `pdsmcp.http`, `pdsmcp.label_parser`, `pdsmcp.metadata`, `pdsmcp.catalog`
4. **Change cache dir** — from project-relative `ppi_data/` to `~/.pdsmcp/data_cache/`
5. **Add stats** — wrap the existing `fetch_ppi_archive_data()` in a `fetch_data()` that computes stats
6. **Update `_get_pds3_slot()`** — load slot from bundled mission JSONs, not xhelio knowledge dir
