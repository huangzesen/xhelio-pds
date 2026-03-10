# PDS Schema Consistency Validation — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Detect schema drift across files within PDS datasets, persist validation records, and surface quality signals via `browse_parameters` and a batch CLI tool.

**Architecture:** New `validation.py` module handles all validation logic (compare labels, persist records, build summaries). `fetch.py` buffers labels during fetch and flushes validations at the end. `metadata.py` includes validation summary in `browse_parameters` response. `cache.py` adds `"validation"` category. New `scripts/validate_schema.py` CLI for batch validation.

**Tech Stack:** Python 3.10+, json, pathlib. No new dependencies.

**Spec:** `docs/specs/2026-03-10-schema-validation-design.md`

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `src/pdsmcp/validation.py` | Create | Core validation logic: extract fields, compare, annotate, persist, summarize |
| `src/pdsmcp/fetch.py` | Modify (lines 192–231) | Buffer labels during file loop, call `flush_validations` after |
| `src/pdsmcp/metadata.py` | Modify (lines 91–115) | Include `get_validation_summary()` in `browse_parameters` response |
| `src/pdsmcp/cache.py` | Modify (lines 210, 250–253) | Add `"validation"` to status/clean categories |
| `src/pdsmcp/scripts/validate_schema.py` | Create | Batch CLI: sample labels across datasets, run validation |
| `tests/test_validation.py` | Create | Unit tests for validation.py |
| `tests/test_validation_integration.py` | Create | Integration tests for fetch→validation→browse_parameters flow |

---

## Chunk 1: Core Validation Module

### Task 1: Create `validation.py` — field extraction and reference schema

**Files:**
- Create: `src/pdsmcp/validation.py`
- Create: `tests/test_validation.py`

- [ ] **Step 1: Write test for `_extract_data_fields`**

This helper extracts non-time fields from a parsed label's `fields` list, returning a dict keyed by field name with metadata (type, units, size, offset, length).

```python
# tests/test_validation.py
"""Tests for PDS schema consistency validation."""
import json
from pathlib import Path

import pytest

from pdsmcp.validation import _extract_data_fields


def _make_label(fields):
    """Helper to build a minimal parsed label dict."""
    return {"fields": fields, "table_type": "fixed"}


class TestExtractDataFields:
    def test_extracts_non_time_fields(self):
        label = _make_label([
            {"name": "TIME", "type": "ASCII_Date_Time", "unit": "", "offset": 1, "length": 24},
            {"name": "BX", "type": "ASCII_Real", "unit": "nT", "offset": 26, "length": 15},
            {"name": "BY", "type": "ASCII_Real", "unit": "nT", "offset": 42, "length": 15},
        ])
        result = _extract_data_fields(label)
        assert "BX" in result
        assert "BY" in result
        assert "TIME" not in result
        assert result["BX"]["units"] == "nT"
        assert result["BX"]["type"] == "ASCII_Real"

    def test_skips_epoch_utc_date(self):
        label = _make_label([
            {"name": "EPOCH", "type": "double", "unit": "", "offset": 1, "length": 20},
            {"name": "UTC", "type": "CHAR", "unit": "", "offset": 21, "length": 24},
            {"name": "SCET", "type": "double", "unit": "", "offset": 45, "length": 15},
            {"name": "VALUE", "type": "double", "unit": "V", "offset": 61, "length": 10},
        ])
        result = _extract_data_fields(label)
        assert list(result.keys()) == ["VALUE"]

    def test_empty_fields(self):
        label = _make_label([])
        result = _extract_data_fields(label)
        assert result == {}

    def test_includes_offset_length_size(self):
        label = _make_label([
            {"name": "BR", "type": "double", "unit": "nT", "offset": 1, "length": 15,
             "description": "radial B"},
        ])
        result = _extract_data_fields(label)
        assert result["BR"]["offset"] == 1
        assert result["BR"]["length"] == 15
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_validation.py::TestExtractDataFields -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pdsmcp.validation'`

- [ ] **Step 3: Implement `_extract_data_fields` and `get_validation_dir`**

```python
# src/pdsmcp/validation.py
"""PDS schema consistency validation.

Detects schema drift across files within a PDS dataset by comparing
each file's label against a reference schema captured from the first
file seen. Persists validation records and annotations to
``~/.pdsmcp/validation/{dataset}.json``.
"""

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

# Time-like field names to exclude from schema comparison
_TIME_NAMES = frozenset({
    "time", "epoch", "utc", "scet", "datetime", "date_time",
    "timestamp", "t", "time_utc", "sample utc",
})

# Tracked metadata attributes for drift detection
_DRIFT_ATTRS = ("units", "type", "size")


def get_validation_dir() -> Path:
    """Return the validation records directory.

    Respects ``PDSMCP_CACHE_DIR`` env var. Defaults to ``~/.pdsmcp/validation/``.
    """
    custom = os.environ.get("PDSMCP_CACHE_DIR")
    if custom:
        return Path(custom) / "validation"
    return Path.home() / ".pdsmcp" / "validation"


def _validation_filename(dataset_id: str) -> str:
    """Sanitize a dataset ID to a safe filename."""
    return dataset_id.replace(":", "_").replace("/", "_") + ".json"


def _extract_data_fields(label: dict) -> dict[str, dict]:
    """Extract non-time fields from a parsed label.

    Args:
        label: Parsed label dict with a ``fields`` list. Each field has
            ``name``, ``type``, ``unit``, ``offset``, ``length``, etc.

    Returns:
        Dict keyed by field name -> {type, units, size, offset, length}.
    """
    result = {}
    for field in label.get("fields", []):
        name = field.get("name", "").strip()
        if not name:
            continue
        if name.lower() in _TIME_NAMES:
            continue
        result[name] = {
            "type": field.get("type", ""),
            "units": field.get("unit", ""),
            "size": field.get("size", [1]) if "size" in field else [1],
            "offset": field.get("offset", 0),
            "length": field.get("length", 0),
        }
    return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_validation.py::TestExtractDataFields -v`
Expected: All 4 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/pdsmcp/validation.py tests/test_validation.py
git commit -m "feat(validation): add field extraction and validation dir helpers"
```

---

### Task 2: Implement `flush_validations` — core comparison and persistence

**Files:**
- Modify: `src/pdsmcp/validation.py`
- Modify: `tests/test_validation.py`

- [ ] **Step 1: Write tests for `flush_validations`**

```python
# Append to tests/test_validation.py

from pdsmcp.validation import flush_validations, get_validation_dir, _validation_filename


class TestFlushValidations:
    def test_first_label_sets_reference_schema(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PDSMCP_CACHE_DIR", str(tmp_path))
        label = _make_label([
            {"name": "TIME", "type": "ASCII_Date_Time", "unit": "", "offset": 1, "length": 24},
            {"name": "BX", "type": "double", "unit": "nT", "offset": 26, "length": 15},
        ])
        flush_validations("test:DATASET", [
            (label, "FILE_001.LBL", "https://archive/FILE_001.LBL"),
        ])

        vfile = tmp_path / "validation" / _validation_filename("test:DATASET")
        assert vfile.exists()
        data = json.loads(vfile.read_text())
        assert data["reference_schema"]["source_file"] == "FILE_001.LBL"
        assert "BX" in data["reference_schema"]["fields"]
        assert "TIME" not in data["reference_schema"]["fields"]
        assert len(data["_validations"]) == 1

    def test_consistent_labels_no_issues(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PDSMCP_CACHE_DIR", str(tmp_path))
        label1 = _make_label([
            {"name": "TIME", "type": "t", "unit": "", "offset": 1, "length": 24},
            {"name": "BX", "type": "double", "unit": "nT", "offset": 26, "length": 15},
            {"name": "BY", "type": "double", "unit": "nT", "offset": 42, "length": 15},
        ])
        label2 = _make_label([
            {"name": "TIME", "type": "t", "unit": "", "offset": 1, "length": 24},
            {"name": "BX", "type": "double", "unit": "nT", "offset": 26, "length": 15},
            {"name": "BY", "type": "double", "unit": "nT", "offset": 42, "length": 15},
        ])
        flush_validations("test:DS", [
            (label1, "F1.LBL", "https://archive/F1.LBL"),
            (label2, "F2.LBL", "https://archive/F2.LBL"),
        ])

        vfile = tmp_path / "validation" / _validation_filename("test:DS")
        data = json.loads(vfile.read_text())
        assert data["schema_annotations"]["BX"]["presence_ratio"] == 1.0
        assert data["schema_annotations"]["BX"]["drift"] == []
        assert len(data["_validations"]) == 2

    def test_missing_field_detected(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PDSMCP_CACHE_DIR", str(tmp_path))
        label_full = _make_label([
            {"name": "BX", "type": "double", "unit": "nT", "offset": 1, "length": 15},
            {"name": "BY", "type": "double", "unit": "nT", "offset": 16, "length": 15},
        ])
        label_partial = _make_label([
            {"name": "BX", "type": "double", "unit": "nT", "offset": 1, "length": 15},
        ])
        flush_validations("test:DS2", [
            (label_full, "F1.LBL", "https://archive/F1.LBL"),
            (label_partial, "F2.LBL", "https://archive/F2.LBL"),
        ])

        vfile = tmp_path / "validation" / _validation_filename("test:DS2")
        data = json.loads(vfile.read_text())
        assert data["schema_annotations"]["BY"]["files_present"] == 1
        assert data["schema_annotations"]["BY"]["presence_ratio"] == 0.5
        assert data["_validations"][1]["missing_fields"] == ["BY"]

    def test_new_field_detected(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PDSMCP_CACHE_DIR", str(tmp_path))
        label1 = _make_label([
            {"name": "BX", "type": "double", "unit": "nT", "offset": 1, "length": 15},
        ])
        label2 = _make_label([
            {"name": "BX", "type": "double", "unit": "nT", "offset": 1, "length": 15},
            {"name": "BZ", "type": "double", "unit": "nT", "offset": 16, "length": 15},
        ])
        flush_validations("test:DS3", [
            (label1, "F1.LBL", "https://archive/F1.LBL"),
            (label2, "F2.LBL", "https://archive/F2.LBL"),
        ])

        vfile = tmp_path / "validation" / _validation_filename("test:DS3")
        data = json.loads(vfile.read_text())
        assert "BZ" in data["schema_annotations"]
        assert data["schema_annotations"]["BZ"]["files_present"] == 1
        assert data["_validations"][1]["new_fields"] == ["BZ"]

    def test_drift_detected(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PDSMCP_CACHE_DIR", str(tmp_path))
        label1 = _make_label([
            {"name": "BX", "type": "double", "unit": "nT", "offset": 1, "length": 15},
        ])
        label2 = _make_label([
            {"name": "BX", "type": "double", "unit": "nanotesla", "offset": 1, "length": 15},
        ])
        flush_validations("test:DS4", [
            (label1, "F1.LBL", "https://archive/F1.LBL"),
            (label2, "F2.LBL", "https://archive/F2.LBL"),
        ])

        vfile = tmp_path / "validation" / _validation_filename("test:DS4")
        data = json.loads(vfile.read_text())
        drift = data["schema_annotations"]["BX"]["drift"]
        assert len(drift) == 1
        assert drift[0]["field"] == "units"
        assert drift[0]["expected"] == "nT"
        assert drift[0]["actual"] == "nanotesla"
        assert drift[0]["first_seen_in"] == "F2.LBL"

    def test_dedup_by_source_url(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PDSMCP_CACHE_DIR", str(tmp_path))
        label = _make_label([
            {"name": "BX", "type": "double", "unit": "nT", "offset": 1, "length": 15},
        ])
        # Flush the same URL twice (two separate calls)
        flush_validations("test:DS5", [
            (label, "F1.LBL", "https://archive/F1.LBL"),
        ])
        flush_validations("test:DS5", [
            (label, "F1.LBL", "https://archive/F1.LBL"),
        ])

        vfile = tmp_path / "validation" / _validation_filename("test:DS5")
        data = json.loads(vfile.read_text())
        assert len(data["_validations"]) == 1  # Not duplicated

    def test_incremental_across_calls(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PDSMCP_CACHE_DIR", str(tmp_path))
        label1 = _make_label([
            {"name": "BX", "type": "double", "unit": "nT", "offset": 1, "length": 15},
        ])
        label2 = _make_label([
            {"name": "BX", "type": "double", "unit": "nT", "offset": 1, "length": 15},
        ])
        flush_validations("test:DS6", [
            (label1, "F1.LBL", "https://archive/F1.LBL"),
        ])
        flush_validations("test:DS6", [
            (label2, "F2.LBL", "https://archive/F2.LBL"),
        ])

        vfile = tmp_path / "validation" / _validation_filename("test:DS6")
        data = json.loads(vfile.read_text())
        assert len(data["_validations"]) == 2
        assert data["schema_annotations"]["BX"]["files_seen"] == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_validation.py::TestFlushValidations -v`
Expected: FAIL — `ImportError: cannot import name 'flush_validations'`

- [ ] **Step 3: Implement `flush_validations`**

Add to `src/pdsmcp/validation.py`:

```python
def flush_validations(
    dataset_id: str,
    pending: list[tuple[dict, str, str]],
) -> None:
    """Validate a batch of labels and persist results.

    Loads the existing validation file (or starts fresh), processes
    each ``(label, source_file, source_url)`` tuple, and writes
    back once.

    Args:
        dataset_id: PDS dataset ID.
        pending: List of ``(parsed_label, source_filename, source_url)``
            tuples to validate.
    """
    if not pending:
        return

    val_dir = get_validation_dir()
    val_dir.mkdir(parents=True, exist_ok=True)
    val_file = val_dir / _validation_filename(dataset_id)

    # Load existing state
    state = _load_validation_state(val_file, dataset_id)
    seen_urls = {v["source_url"] for v in state["_validations"]}

    for label, source_file, source_url in pending:
        if source_url in seen_urls:
            continue
        seen_urls.add(source_url)

        current_fields = _extract_data_fields(label)
        all_field_names = [f.get("name", "") for f in label.get("fields", [])]
        record = {
            "version": len(state["_validations"]) + 1,
            "source_file": source_file,
            "source_url": source_url,
            "validated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "fields_in_label": all_field_names,
            "new_fields": [],
            "missing_fields": [],
            "drift": [],
        }

        # First label: set reference schema
        if state["reference_schema"] is None:
            state["reference_schema"] = {
                "source_file": source_file,
                "source_url": source_url,
                "captured_at": record["validated_at"],
                "fields": current_fields,
            }
            # Initialize annotations for all fields
            for name in current_fields:
                state["schema_annotations"][name] = {
                    "files_seen": 1,
                    "files_present": 1,
                    "presence_ratio": 1.0,
                    "drift": [],
                }
            state["_validations"].append(record)
            continue

        ref_fields = state["reference_schema"]["fields"]
        ref_names = set(ref_fields.keys())
        cur_names = set(current_fields.keys())

        # Missing: in reference but not this label
        missing = sorted(ref_names - cur_names)
        record["missing_fields"] = missing

        # New: in this label but not in reference
        new = sorted(cur_names - ref_names)
        record["new_fields"] = new

        # Drift: same name, different metadata
        drift_entries = []
        for name in ref_names & cur_names:
            for attr in _DRIFT_ATTRS:
                ref_val = ref_fields[name].get(attr)
                cur_val = current_fields[name].get(attr)
                if ref_val != cur_val:
                    drift_entry = {
                        "field": attr,
                        "expected": ref_val,
                        "actual": cur_val,
                        "first_seen_in": source_file,
                    }
                    drift_entries.append(drift_entry)
        record["drift"] = drift_entries

        # Update schema_annotations
        annotations = state["schema_annotations"]

        # Increment files_seen for all known fields
        for name in annotations:
            annotations[name]["files_seen"] += 1

        # Update presence for reference fields
        for name in ref_names:
            if name in cur_names:
                annotations[name]["files_present"] += 1
            # Recalculate ratio
            ann = annotations[name]
            ann["presence_ratio"] = round(
                ann["files_present"] / ann["files_seen"], 4
            )

        # Add new fields to annotations
        total_files = annotations[next(iter(ref_names))]["files_seen"] if ref_names else 1
        for name in new:
            annotations[name] = {
                "files_seen": total_files,
                "files_present": 1,
                "presence_ratio": round(1 / total_files, 4),
                "drift": [],
            }

        # Append drift to annotations (only first occurrence per attr)
        for de in drift_entries:
            # Find which field this drift is about — it's about the reference field
            # We need to figure out the parameter name from the drift entry
            pass
        # Better approach: iterate by field name
        for name in ref_names & cur_names:
            for attr in _DRIFT_ATTRS:
                ref_val = ref_fields[name].get(attr)
                cur_val = current_fields[name].get(attr)
                if ref_val != cur_val:
                    # Only add if this exact drift not already recorded
                    existing = annotations[name]["drift"]
                    already = any(
                        d["field"] == attr and d["actual"] == cur_val
                        for d in existing
                    )
                    if not already:
                        annotations[name]["drift"].append({
                            "field": attr,
                            "expected": ref_val,
                            "actual": cur_val,
                            "first_seen_in": source_file,
                        })

        state["_validations"].append(record)

    # Write back
    with open(val_file, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)
        f.write("\n")


def _load_validation_state(val_file: Path, dataset_id: str) -> dict:
    """Load existing validation state or create empty."""
    if val_file.exists():
        try:
            with open(val_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {
        "dataset_id": dataset_id,
        "reference_schema": None,
        "schema_annotations": {},
        "_validations": [],
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_validation.py -v`
Expected: All 11 tests PASS

- [ ] **Step 5: Smoke-test import**

Run: `python -c "import pdsmcp.validation; print('OK')"`
Expected: `OK`

- [ ] **Step 6: Commit**

```bash
git add src/pdsmcp/validation.py tests/test_validation.py
git commit -m "feat(validation): implement flush_validations with drift detection"
```

---

### Task 3: Implement `get_validation_summary`

**Files:**
- Modify: `src/pdsmcp/validation.py`
- Modify: `tests/test_validation.py`

- [ ] **Step 1: Write tests for `get_validation_summary`**

```python
# Append to tests/test_validation.py

from pdsmcp.validation import get_validation_summary


class TestGetValidationSummary:
    def test_returns_none_when_no_validation(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PDSMCP_CACHE_DIR", str(tmp_path))
        result = get_validation_summary("nonexistent:DATASET")
        assert result is None

    def test_clean_dataset(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PDSMCP_CACHE_DIR", str(tmp_path))
        label = _make_label([
            {"name": "BX", "type": "double", "unit": "nT", "offset": 1, "length": 15},
            {"name": "BY", "type": "double", "unit": "nT", "offset": 16, "length": 15},
        ])
        flush_validations("test:CLEAN", [
            (label, "F1.LBL", "https://archive/F1.LBL"),
            (label, "F2.LBL", "https://archive/F2.LBL"),
        ])
        result = get_validation_summary("test:CLEAN")
        assert result["validated"] is True
        assert result["files_checked"] == 2
        assert result["issues"] == []
        assert "no issues" in result["summary"]

    def test_presence_issue(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PDSMCP_CACHE_DIR", str(tmp_path))
        label1 = _make_label([
            {"name": "BX", "type": "double", "unit": "nT", "offset": 1, "length": 15},
            {"name": "BY", "type": "double", "unit": "nT", "offset": 16, "length": 15},
        ])
        label2 = _make_label([
            {"name": "BX", "type": "double", "unit": "nT", "offset": 1, "length": 15},
        ])
        flush_validations("test:PARTIAL", [
            (label1, "F1.LBL", "https://a/F1.LBL"),
            (label2, "F2.LBL", "https://a/F2.LBL"),
        ])
        result = get_validation_summary("test:PARTIAL")
        assert len(result["issues"]) == 1
        assert result["issues"][0]["parameter"] == "BY"
        assert result["issues"][0]["presence_ratio"] == 0.5

    def test_drift_issue(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PDSMCP_CACHE_DIR", str(tmp_path))
        label1 = _make_label([
            {"name": "BX", "type": "double", "unit": "nT", "offset": 1, "length": 15},
        ])
        label2 = _make_label([
            {"name": "BX", "type": "double", "unit": "nanotesla", "offset": 1, "length": 15},
        ])
        flush_validations("test:DRIFT", [
            (label1, "F1.LBL", "https://a/F1.LBL"),
            (label2, "F2.LBL", "https://a/F2.LBL"),
        ])
        result = get_validation_summary("test:DRIFT")
        drift_issues = [i for i in result["issues"] if i.get("type") == "drift"]
        assert len(drift_issues) == 1
        assert drift_issues[0]["field"] == "units"
        assert drift_issues[0]["expected"] == "nT"
        assert drift_issues[0]["actual"] == "nanotesla"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_validation.py::TestGetValidationSummary -v`
Expected: FAIL — `ImportError: cannot import name 'get_validation_summary'`

- [ ] **Step 3: Implement `get_validation_summary`**

Add to `src/pdsmcp/validation.py`:

```python
def get_validation_summary(dataset_id: str) -> dict | None:
    """Build a validation summary for a dataset.

    Reads the validation file and produces the ``"validation"`` dict
    for inclusion in ``browse_parameters`` responses.

    Args:
        dataset_id: PDS dataset ID.

    Returns:
        Summary dict with ``validated``, ``files_checked``, ``last_validated``,
        ``issues``, and ``summary``.  Returns ``None`` if no validation
        file exists.
    """
    val_dir = get_validation_dir()
    val_file = val_dir / _validation_filename(dataset_id)
    if not val_file.exists():
        return None

    try:
        with open(val_file, "r", encoding="utf-8") as f:
            state = json.load(f)
    except (json.JSONDecodeError, OSError):
        return None

    validations = state.get("_validations", [])
    annotations = state.get("schema_annotations", {})

    if not validations:
        return None

    files_checked = len(validations)
    last_validated = validations[-1].get("validated_at", "")

    issues = []
    for param_name, ann in sorted(annotations.items()):
        ratio = ann.get("presence_ratio", 1.0)
        if ratio < 1.0:
            files_present = ann.get("files_present", 0)
            files_seen = ann.get("files_seen", 0)
            issues.append({
                "parameter": param_name,
                "presence_ratio": ratio,
                "note": f"missing from {files_seen - files_present} of {files_seen} files",
            })
        for drift in ann.get("drift", []):
            issues.append({
                "parameter": param_name,
                "type": "drift",
                "field": drift["field"],
                "expected": drift["expected"],
                "actual": drift["actual"],
                "note": f"{drift['field']} changed in {drift['first_seen_in']}",
            })

    if issues:
        summary = f"{len(issues)} issue(s) across {files_checked} files checked"
    else:
        summary = f"no issues across {files_checked} files checked"

    return {
        "validated": True,
        "files_checked": files_checked,
        "last_validated": last_validated,
        "issues": issues,
        "summary": summary,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_validation.py -v`
Expected: All 15 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/pdsmcp/validation.py tests/test_validation.py
git commit -m "feat(validation): implement get_validation_summary for browse_parameters"
```

---

## Chunk 2: Integration into Existing Modules

### Task 4: Integrate validation into `fetch.py`

**Files:**
- Modify: `src/pdsmcp/fetch.py` (lines 192–231)

- [ ] **Step 1: Add import at top of `fetch.py`**

At the top of `src/pdsmcp/fetch.py`, add the import alongside the existing imports:

```python
from pdsmcp.validation import flush_validations
```

Note: If this creates a circular import, use a lazy import inside `_fetch_single_parameter` instead:
```python
from pdsmcp.validation import flush_validations
```

- [ ] **Step 2: Add validation buffering to `_fetch_single_parameter`**

In `src/pdsmcp/fetch.py`, modify the file loop in `_fetch_single_parameter()` (currently lines 192–231).

Before the loop (line 195, after `skipped = []`), add:

```python
    pending_validations = []
```

Inside the loop, after `label = _parse_label(local_label)` (line 201), add:

```python
            pending_validations.append((label, label_url.rsplit("/", 1)[-1], label_url))
```

After the loop ends (after line 231, before the `if not frames:` check at line 232), add:

```python
    # Flush schema validation records
    try:
        flush_validations(dataset_id, pending_validations)
    except Exception as e:
        logger.warning("Schema validation failed for %s: %s", dataset_id, e)
```

The try/except ensures validation errors never break the fetch pipeline.

- [ ] **Step 3: Smoke-test the import**

Run: `python -c "from pdsmcp.fetch import fetch_data; print('OK')"`
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add src/pdsmcp/fetch.py
git commit -m "feat(validation): integrate schema validation into fetch pipeline"
```

---

### Task 5: Surface validation in `browse_parameters`

**Files:**
- Modify: `src/pdsmcp/metadata.py` (lines 91–115)

- [ ] **Step 1: Add import to `metadata.py`**

At the top of `src/pdsmcp/metadata.py`, add:

```python
from pdsmcp.validation import get_validation_summary
```

- [ ] **Step 2: Modify `browse_parameters` to include validation**

In `src/pdsmcp/metadata.py`, in the `browse_parameters` function, after building each `entry` dict (around line 104, after the `if start or stop:` block), add:

```python
            # Include schema validation summary if available
            validation = get_validation_summary(ds_id)
            entry["validation"] = validation
```

This goes inside the `try` block, after line 104 (`entry["time_range"] = ...`), before the `except` at line 105.

- [ ] **Step 3: Smoke-test the import**

Run: `python -c "from pdsmcp.metadata import browse_parameters; print('OK')"`
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add src/pdsmcp/metadata.py
git commit -m "feat(validation): surface validation summary in browse_parameters"
```

---

### Task 6: Add validation to cache management

**Files:**
- Modify: `src/pdsmcp/cache.py` (lines 210, 250–253)

- [ ] **Step 1: Modify `cache_status` to include validation category**

In `src/pdsmcp/cache.py`, change the category loop in `cache_status()` (line 210):

```python
    for name in ("metadata", "data_cache", "validation"):
```

- [ ] **Step 2: Modify `cache_clean` to support validation category**

In `src/pdsmcp/cache.py`, in `cache_clean()`, update the `category == "all"` branch (line 251):

```python
        targets = ["metadata", "data_cache", "validation"]
```

- [ ] **Step 3: Verify the changes work**

Run: `python -c "from pdsmcp.cache import cache_status; import json; print(json.dumps(cache_status(), indent=2))"`
Expected: JSON output with three categories: `metadata`, `data_cache`, `validation`

- [ ] **Step 4: Commit**

```bash
git add src/pdsmcp/cache.py
git commit -m "feat(validation): add validation category to cache management"
```

---

## Chunk 3: Batch Validation CLI

### Task 7: Create `scripts/validate_schema.py`

**Files:**
- Create: `src/pdsmcp/scripts/validate_schema.py`

- [ ] **Step 1: Create the CLI script**

```python
# src/pdsmcp/scripts/validate_schema.py
#!/usr/bin/env python3
"""Batch PDS schema validation — sample labels across datasets.

Downloads and parses N labels per dataset (without fetching full data)
to detect schema drift. Results are persisted to the validation cache.

Usage:
    python -m pdsmcp.scripts.validate_schema                     # All missions
    python -m pdsmcp.scripts.validate_schema --mission juno      # One mission
    python -m pdsmcp.scripts.validate_schema --dataset-id X      # One dataset
    python -m pdsmcp.scripts.validate_schema --sample 20         # More samples
"""

import argparse
import json
import logging
import sys
import time
from pathlib import Path

from pdsmcp.catalog import get_missions_dir, load_mission_json
from pdsmcp.fetch import (
    _discover_data_files,
    _download_file,
    _parse_label,
    _resolve_collection_url,
)
from pdsmcp.validation import flush_validations, get_validation_summary

logger = logging.getLogger(__name__)


def _sample_indices(total: int, n: int) -> list[int]:
    """Return n evenly-spaced indices from [0, total)."""
    if total <= n:
        return list(range(total))
    if n == 1:
        return [0]
    step = (total - 1) / (n - 1)
    return sorted(set(int(round(i * step)) for i in range(n)))


def validate_dataset(dataset_id: str, sample_n: int = 10) -> dict:
    """Validate a single dataset by sampling labels.

    Args:
        dataset_id: PDS dataset ID.
        sample_n: Number of labels to sample.

    Returns:
        Dict with status, files_sampled, issues_count.
    """
    try:
        collection_url = _resolve_collection_url(dataset_id)
    except Exception as e:
        return {"status": "error", "message": f"resolve failed: {e}"}

    # Use a wide time range to discover all files
    try:
        file_pairs = _discover_data_files(collection_url, "1970-01-01", "2099-12-31")
    except Exception as e:
        return {"status": "error", "message": f"discovery failed: {e}"}

    if not file_pairs:
        return {"status": "error", "message": "no files found"}

    # Sample labels evenly across the file list
    indices = _sample_indices(len(file_pairs), sample_n)
    sampled_pairs = [file_pairs[i] for i in indices]

    pending = []
    errors = 0
    for data_url, label_url in sampled_pairs:
        try:
            local_label = _download_file(label_url)
            label = _parse_label(local_label)
            source_file = label_url.rsplit("/", 1)[-1]
            pending.append((label, source_file, label_url))
        except Exception as e:
            logger.warning("Failed to parse %s: %s", label_url, e)
            errors += 1

    if pending:
        flush_validations(dataset_id, pending)

    summary = get_validation_summary(dataset_id)
    issues_count = len(summary["issues"]) if summary else 0

    return {
        "status": "ok",
        "files_sampled": len(pending),
        "files_failed": errors,
        "issues_count": issues_count,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Batch PDS schema validation"
    )
    parser.add_argument(
        "--mission", type=str, default=None,
        help="Validate only one mission (e.g., 'juno')",
    )
    parser.add_argument(
        "--dataset-id", type=str, default=None,
        help="Validate a single dataset ID",
    )
    parser.add_argument(
        "--sample", type=int, default=10,
        help="Number of labels to sample per dataset (default: 10)",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if args.dataset_id:
        print(f"Validating {args.dataset_id} (sample={args.sample})...")
        result = validate_dataset(args.dataset_id, args.sample)
        print(json.dumps(result, indent=2))
        return

    # Enumerate datasets from mission JSONs
    missions_dir = get_missions_dir()
    total_datasets = 0
    total_issues = 0
    start_time = time.time()

    for filepath in sorted(missions_dir.glob("*.json")):
        stem = filepath.stem
        if args.mission and stem != args.mission:
            continue

        try:
            mission_data = load_mission_json(stem)
        except Exception:
            continue

        datasets = []
        for inst in mission_data.get("instruments", {}).values():
            for ds_id in inst.get("datasets", {}):
                datasets.append(ds_id)

        if not datasets:
            continue

        print(f"\n{stem}: {len(datasets)} datasets")
        for ds_id in datasets:
            total_datasets += 1
            result = validate_dataset(ds_id, args.sample)
            status = result.get("status", "error")
            if status == "ok":
                issues = result.get("issues_count", 0)
                total_issues += issues
                sampled = result.get("files_sampled", 0)
                marker = f" *** {issues} issue(s)" if issues else ""
                print(f"  {ds_id} — {sampled} files checked{marker}")
            else:
                msg = result.get("message", "unknown error")
                print(f"  {ds_id} — ERROR: {msg}")

    elapsed = time.time() - start_time
    print(f"\nDone: {total_datasets} datasets, {total_issues} issues, {elapsed:.0f}s")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Verify the script imports correctly**

Run: `python -c "from pdsmcp.scripts.validate_schema import validate_dataset; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add src/pdsmcp/scripts/validate_schema.py
git commit -m "feat(validation): add batch schema validation CLI script"
```

---

## Chunk 4: Final Verification

### Task 8: Integration test and final verification

**Files:**
- Create: `tests/test_validation_integration.py`

- [ ] **Step 1: Write integration test for the full flow**

This test mocks the fetch internals to verify that `_fetch_single_parameter` → `flush_validations` → `get_validation_summary` → `browse_parameters` all connect properly.

```python
# tests/test_validation_integration.py
"""Integration tests for validation flow: fetch → validation → browse_parameters."""
import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from pdsmcp.validation import flush_validations, get_validation_summary, _validation_filename


class TestValidationInBrowseParameters:
    """Test that browse_parameters includes validation data."""

    def test_browse_parameters_includes_validation(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PDSMCP_CACHE_DIR", str(tmp_path))

        # Create a fake metadata cache
        meta_dir = tmp_path / "metadata"
        meta_dir.mkdir()
        meta_file = meta_dir / "test_DATASET.json"
        meta_file.write_text(json.dumps({
            "parameters": [
                {"name": "Time", "type": "isotime", "length": 24},
                {"name": "BX", "type": "double", "units": "nT", "description": "B radial"},
            ],
            "startDate": "2024-01-01",
            "stopDate": "2024-12-31",
        }))

        # Create validation records
        from pdsmcp.validation import _extract_data_fields
        label1 = {"fields": [
            {"name": "BX", "type": "double", "unit": "nT", "offset": 1, "length": 15},
            {"name": "BY", "type": "double", "unit": "nT", "offset": 16, "length": 15},
        ], "table_type": "fixed"}
        label2 = {"fields": [
            {"name": "BX", "type": "double", "unit": "nT", "offset": 1, "length": 15},
        ], "table_type": "fixed"}

        flush_validations("test:DATASET", [
            (label1, "F1.LBL", "https://a/F1"),
            (label2, "F2.LBL", "https://a/F2"),
        ])

        # Patch _dataset_id_to_cache_filename to match our test file
        with patch("pdsmcp.metadata._dataset_id_to_cache_filename", return_value="test_DATASET.json"):
            from pdsmcp.metadata import browse_parameters
            result = browse_parameters(dataset_id="test:DATASET")

        assert result["status"] == "success"
        assert result["validation"] is not None
        assert result["validation"]["files_checked"] == 2
        assert len(result["validation"]["issues"]) == 1
        assert result["validation"]["issues"][0]["parameter"] == "BY"

    def test_browse_parameters_null_when_no_validation(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PDSMCP_CACHE_DIR", str(tmp_path))

        meta_dir = tmp_path / "metadata"
        meta_dir.mkdir()
        meta_file = meta_dir / "test_NOVAL.json"
        meta_file.write_text(json.dumps({
            "parameters": [
                {"name": "Time", "type": "isotime", "length": 24},
                {"name": "BX", "type": "double", "units": "nT", "description": ""},
            ],
        }))

        with patch("pdsmcp.metadata._dataset_id_to_cache_filename", return_value="test_NOVAL.json"):
            from pdsmcp.metadata import browse_parameters
            result = browse_parameters(dataset_id="test:NOVAL")

        assert result["status"] == "success"
        assert result["validation"] is None
```

- [ ] **Step 2: Run all tests**

Run: `python -m pytest tests/test_validation.py tests/test_validation_integration.py -v`
Expected: All tests PASS

- [ ] **Step 3: Run full test suite to check for regressions**

Run: `python -m pytest tests/ -v`
Expected: All tests PASS (including any pre-existing tests)

- [ ] **Step 4: Smoke-test all modules**

Run:
```bash
python -c "import pdsmcp.validation; print('validation OK')"
python -c "import pdsmcp.fetch; print('fetch OK')"
python -c "import pdsmcp.metadata; print('metadata OK')"
python -c "import pdsmcp.cache; print('cache OK')"
python -c "from pdsmcp.scripts.validate_schema import main; print('validate_schema OK')"
```
Expected: All print `OK`

- [ ] **Step 5: Commit integration tests**

```bash
git add tests/test_validation_integration.py
git commit -m "test(validation): add integration tests for validation flow"
```

- [ ] **Step 6: Final commit with all changes**

Run `git status` to verify no uncommitted changes. If clean, the implementation is complete.
