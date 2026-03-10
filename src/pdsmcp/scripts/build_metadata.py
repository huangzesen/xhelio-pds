#!/usr/bin/env python3
"""Build bundled parameter metadata JSONs from PDS labels.

Downloads one label per dataset, parses it, and writes the metadata to
``src/pdsmcp/data/metadata/``.  These files ship with the package so
``browse_parameters`` works instantly without network access.

Usage:
    python -m pdsmcp.scripts.build_metadata                  # All missions
    python -m pdsmcp.scripts.build_metadata --mission juno   # One mission
"""

import argparse
import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from pdsmcp.catalog import get_missions_dir, load_mission_json
from pdsmcp.metadata import _fetch_metadata_from_label, _dataset_id_to_cache_filename

logger = logging.getLogger(__name__)

# Bundled metadata lives alongside mission JSONs
_BUNDLED_DIR = Path(__file__).resolve().parent.parent / "data" / "metadata"


def _build_one(dataset_id: str) -> tuple[str, str, int]:
    """Fetch metadata for one dataset. Returns (dataset_id, status, param_count)."""
    try:
        result = _fetch_metadata_from_label(dataset_id)
        if result is None:
            return dataset_id, "no_label", 0
        params = [p for p in result.get("parameters", []) if p.get("name") != "Time"]
        if not params:
            return dataset_id, "no_params", 0

        filename = _dataset_id_to_cache_filename(dataset_id)
        out_path = _BUNDLED_DIR / filename
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, ensure_ascii=False)
            f.write("\n")
        return dataset_id, "ok", len(params)
    except Exception as e:
        return dataset_id, f"error: {e}", 0


def build_metadata(mission: str | None = None, workers: int = 5) -> None:
    """Build metadata for all datasets."""
    _BUNDLED_DIR.mkdir(parents=True, exist_ok=True)

    missions_dir = get_missions_dir()
    datasets: list[str] = []

    for filepath in sorted(missions_dir.glob("*.json")):
        stem = filepath.stem
        if mission and stem != mission:
            continue
        try:
            mission_data = load_mission_json(stem)
        except Exception:
            continue
        for inst in mission_data.get("instruments", {}).values():
            for ds_id in inst.get("datasets", {}):
                datasets.append(ds_id)

    logger.info("Building metadata for %d datasets (workers=%d)", len(datasets), workers)

    ok = 0
    failed = 0
    no_label = 0
    start_time = time.time()

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_build_one, ds_id): ds_id for ds_id in datasets}
        for future in as_completed(futures):
            ds_id, status, param_count = future.result()
            if status == "ok":
                ok += 1
                logger.info("  OK  %s (%d params)", ds_id, param_count)
            elif status == "no_label":
                no_label += 1
                logger.warning("  SKIP %s (no label found)", ds_id)
            elif status == "no_params":
                no_label += 1
                logger.warning("  SKIP %s (no numeric params)", ds_id)
            else:
                failed += 1
                logger.error("  FAIL %s: %s", ds_id, status)

    elapsed = time.time() - start_time
    logger.info(
        "Done in %.0fs: %d ok, %d no_label, %d failed (of %d total)",
        elapsed, ok, no_label, failed, len(datasets),
    )


def main():
    parser = argparse.ArgumentParser(description="Build bundled parameter metadata from PDS labels")
    parser.add_argument("--mission", type=str, default=None, help="Build only one mission")
    parser.add_argument("--workers", type=int, default=5, help="Parallel workers (default: 5)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    build_metadata(mission=args.mission, workers=args.workers)


if __name__ == "__main__":
    main()
