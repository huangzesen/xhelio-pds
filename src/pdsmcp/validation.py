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
        for name in sorted(ref_names & cur_names):
            for attr in _DRIFT_ATTRS:
                ref_val = ref_fields[name].get(attr)
                cur_val = current_fields[name].get(attr)
                if ref_val != cur_val:
                    drift_entries.append({
                        "parameter": name,
                        "field": attr,
                        "expected": ref_val,
                        "actual": cur_val,
                        "first_seen_in": source_file,
                    })
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

        # Append drift to per-field annotations (first occurrence only)
        for de in drift_entries:
            param_name = de["parameter"]
            existing = annotations[param_name]["drift"]
            already = any(
                d["field"] == de["field"] and d["actual"] == de["actual"]
                for d in existing
            )
            if not already:
                annotations[param_name]["drift"].append({
                    "field": de["field"],
                    "expected": de["expected"],
                    "actual": de["actual"],
                    "first_seen_in": de["first_seen_in"],
                })

        state["_validations"].append(record)

    # Write back
    with open(val_file, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)
        f.write("\n")


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
