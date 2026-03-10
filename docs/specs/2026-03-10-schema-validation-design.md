# PDS Schema Consistency Validation — Design Spec

## Problem

PDS datasets can exhibit schema drift: different files within the same dataset may have different field names, units, types, or sizes. This happens because each PDS file has its own label, and labels can change across archive versions, reprocessing campaigns, or time periods. Currently, pdsmcp silently skips files with unexpected schemas — the user gets partial data with no warning.

## Goals

1. Detect schema inconsistencies across files within a PDS dataset
2. Surface validation results to LLM agents via `browse_parameters`
3. Persist validation records for traceability
4. Provide a CLI for batch validation across missions

## Design Decisions

| Decision | Choice | Rationale |
|---|---|---|
| When to validate | Every file during fetch | Catches all drift, dedup prevents double-counting |
| Storage location | Separate files at `~/.pdsmcp/validation/{dataset}.json` | Keeps metadata cache clean, mirrors CDAWeb override pattern |
| What to track | Presence ratio + field metadata drift (units, type, size) | Presence ratio gives actionable signal; drift catches real PDS issues |
| How to surface | Top-level `"validation"` section in `browse_parameters` | Avoids breaking parameter list schema; LLM reads summary separately |

## Data Model

### Validation File (`~/.pdsmcp/validation/{safe_dataset_id}.json`)

```json
{
  "dataset_id": "pds3:JNO-J-3-FGM-CAL-V1.0:DATA",
  "reference_schema": {
    "source_file": "FGM_JNO_L3_2024001SE_V01.LBL",
    "source_url": "https://...",
    "captured_at": "2026-03-10T12:00:00Z",
    "fields": {
      "DECIMAL DAY": {"type": "double", "units": "N/A", "size": [1], "offset": 26, "length": 15},
      "BX PLANETOCENTRIC": {"type": "double", "units": "NT", "size": [1], "offset": 42, "length": 15}
    }
  },
  "schema_annotations": {
    "BX PLANETOCENTRIC": {
      "files_seen": 12,
      "files_present": 12,
      "presence_ratio": 1.0,
      "drift": []
    },
    "DECIMAL DAY": {
      "files_seen": 12,
      "files_present": 10,
      "presence_ratio": 0.83,
      "drift": [
        {"field": "units", "expected": "N/A", "actual": "DAYS", "first_seen_in": "FGM_JNO_L3_2024100SE_V01.LBL"}
      ]
    }
  },
  "_validations": [
    {
      "version": 1,
      "source_file": "FGM_JNO_L3_2024001SE_V01.LBL",
      "source_url": "https://...",
      "validated_at": "2026-03-10T12:00:00Z",
      "fields_in_label": ["TIME", "DECIMAL DAY", "BX PLANETOCENTRIC", "BY PLANETOCENTRIC"],
      "new_fields": [],
      "missing_fields": [],
      "drift": []
    }
  ]
}
```

### Surfacing in `browse_parameters`

```json
{
  "status": "success",
  "dataset_id": "pds3:JNO-J-3-FGM-CAL-V1.0:DATA",
  "parameters": [
    {"name": "BX PLANETOCENTRIC", "type": "double", "units": "NT", "description": "..."}
  ],
  "validation": {
    "validated": true,
    "files_checked": 12,
    "last_validated": "2026-03-10T12:00:00Z",
    "issues": [
      {
        "parameter": "DECIMAL DAY",
        "presence_ratio": 0.83,
        "note": "missing from 2 of 12 files"
      },
      {
        "parameter": "DECIMAL DAY",
        "type": "drift",
        "field": "units",
        "expected": "N/A",
        "actual": "DAYS",
        "note": "units changed in FGM_JNO_L3_2024100SE_V01.LBL"
      }
    ],
    "summary": "2 issues across 12 files checked"
  }
}
```

If no validation exists: `"validation": null`.

## Architecture

### New file: `src/pdsmcp/validation.py` (~150 lines)

- `validate_label(dataset_id, label, source_file, source_url)` — compares a parsed label against the reference schema, updates annotations and appends a validation record. Deduplicates by `source_url`.
- `flush_validations(dataset_id, pending)` — batch version: takes a list of `(label, source_file, source_url)` tuples, loads the validation file once, processes all, writes once.
- `get_validation_summary(dataset_id)` — reads validation file, builds the `"validation"` dict for `browse_parameters`.
- `get_validation_dir()` — returns `~/.pdsmcp/validation/` (respects `PDSMCP_CACHE_DIR`).

### Validation logic in `validate_label()`

1. Load validation file or start empty
2. Dedup: skip if `source_url` already in `_validations`
3. Extract non-time fields from label (`{name: {type, units, size, offset, length}}`)
4. If no `reference_schema` yet: set from this label, return (first file = baseline)
5. Compare against reference:
   - **Missing fields**: in reference but not in this label
   - **New fields**: in this label but not in reference
   - **Drift**: same name, different `units`, `type`, or `size`
6. Update `schema_annotations`: increment counters, append drift entries
7. Append validation record to `_validations`

Time field filtering: skip fields whose name contains `TIME`, `EPOCH`, `UTC`, or `DATE` (case-insensitive), matching existing `_find_time_column()` logic.

### Modified files

| File | Change |
|---|---|
| `fetch.py` | In `_fetch_single_parameter()`, buffer `(label, filename, url)` during file loop, call `flush_validations()` after loop |
| `metadata.py` | In `browse_parameters()`, call `get_validation_summary()` and include in response |
| `cache.py` | Add `"validation"` to `cache_status()` and `cache_clean()` categories |
| `server.py` | No change needed |

### Integration in fetch pipeline

```python
# In _fetch_single_parameter(), inside the file loop:
pending_validations = []
for data_url, label_url in file_pairs:
    label = _parse_label(label_text, label_url)
    pending_validations.append((label, label_url.split("/")[-1], label_url))
    # ... existing read_table logic ...

# After the loop:
flush_validations(dataset_id, pending_validations)
```

Buffering avoids repeated file I/O — load once, write once per fetch.

### New script: `scripts/validate_schema.py`

CLI for batch label sampling without downloading full data files.

```bash
python -m pdsmcp.scripts.validate_schema --mission juno
python -m pdsmcp.scripts.validate_schema --dataset-id "pds3:JNO-J-3-FGM-CAL-V1.0:DATA" --sample 20
python -m pdsmcp.scripts.validate_schema  # all missions
```

Reuses `_resolve_collection_url`, `_discover_data_files`, `_download_file`, `_parse_label` from fetch.py. Samples N labels (default 10) spread evenly across the file list (first, last, evenly spaced between).

### Cache management

`cache_status()` adds `"validation"` category scanning `~/.pdsmcp/validation/`. `cache_clean()` supports `category="validation"` and `category="all"` includes it.
