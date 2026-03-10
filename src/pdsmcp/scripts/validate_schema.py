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

    try:
        file_pairs = _discover_data_files(collection_url, "1970-01-01", "2099-12-31")
    except Exception as e:
        return {"status": "error", "message": f"discovery failed: {e}"}

    if not file_pairs:
        return {"status": "error", "message": "no files found"}

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
