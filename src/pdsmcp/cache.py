"""Cache management — status, cleanup, metadata refresh, catalog rebuild."""

import json
import logging
import os
import re
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

_SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def _get_cache_root() -> Path:
    """Return the cache root directory."""
    custom = os.environ.get("PDSMCP_CACHE_DIR")
    if custom:
        return Path(custom)
    return Path.home() / ".pdsmcp"


def _get_missions_dir():
    """Return the bundled missions directory."""
    from pdsmcp.catalog import get_missions_dir
    return get_missions_dir()


def _validate_name(name: str) -> str:
    """Validate a single path component (no traversal)."""
    if not name or not _SAFE_NAME_RE.match(name) or ".." in name:
        raise ValueError(f"Invalid name: {name!r}")
    return name


def _format_bytes(n: int) -> str:
    """Human-readable size (e.g., '2.3 GB')."""
    if n < 1024:
        return f"{n} B"
    for unit in ("KB", "MB", "GB", "TB"):
        n /= 1024
        if n < 1024 or unit == "TB":
            return f"{n:.1f} {unit}"
    return f"{n:.1f} TB"


# ---------------------------------------------------------------------------
# Scanning
# ---------------------------------------------------------------------------

def _scan_directory(path: Path) -> dict:
    """Recursively scan a directory for file count and total bytes."""
    total_bytes = 0
    file_count = 0
    oldest_mtime = None
    newest_mtime = None

    def _walk(p: str) -> None:
        nonlocal total_bytes, file_count, oldest_mtime, newest_mtime
        try:
            with os.scandir(p) as it:
                for entry in it:
                    if entry.is_file(follow_symlinks=False):
                        try:
                            stat = entry.stat(follow_symlinks=False)
                        except OSError:
                            continue
                        total_bytes += stat.st_size
                        file_count += 1
                        mt = stat.st_mtime
                        if oldest_mtime is None or mt < oldest_mtime:
                            oldest_mtime = mt
                        if newest_mtime is None or mt > newest_mtime:
                            newest_mtime = mt
                    elif entry.is_dir(follow_symlinks=False):
                        _walk(entry.path)
        except PermissionError:
            pass

    if path.exists():
        _walk(str(path))

    return {
        "path": str(path),
        "total_bytes": total_bytes,
        "total_human": _format_bytes(total_bytes),
        "file_count": file_count,
        "oldest_mtime": oldest_mtime,
        "newest_mtime": newest_mtime,
    }


def _scan_subdirectories(root: Path) -> list[dict]:
    """Scan each immediate subdirectory of root."""
    results = []
    if not root.exists():
        return results
    try:
        entries = sorted(root.iterdir())
    except PermissionError:
        return results
    for child in entries:
        if child.is_dir():
            stats = _scan_directory(child)
            stats["name"] = child.name
            results.append(stats)
    return results


# ---------------------------------------------------------------------------
# Cleanup helpers
# ---------------------------------------------------------------------------

def _delete_old_files(root: Path, older_than_days: int) -> tuple[int, int]:
    """Delete files older than N days within root."""
    cutoff = time.time() - older_than_days * 86400
    deleted_count = 0
    freed_bytes = 0

    def _walk(p: str) -> None:
        nonlocal deleted_count, freed_bytes
        try:
            with os.scandir(p) as it:
                for entry in it:
                    if entry.is_file(follow_symlinks=False):
                        try:
                            stat = entry.stat(follow_symlinks=False)
                        except OSError:
                            continue
                        if stat.st_mtime < cutoff:
                            size = stat.st_size
                            try:
                                os.unlink(entry.path)
                                deleted_count += 1
                                freed_bytes += size
                            except OSError:
                                pass
                    elif entry.is_dir(follow_symlinks=False):
                        _walk(entry.path)
        except PermissionError:
            pass

    if root.exists():
        _walk(str(root))
    _remove_empty_dirs(root)
    return deleted_count, freed_bytes


def _count_old_files(root: Path, older_than_days: int) -> tuple[int, int]:
    """Count files and bytes older than N days (for dry_run)."""
    cutoff = time.time() - older_than_days * 86400
    count = 0
    total_bytes = 0

    def _walk(p: str) -> None:
        nonlocal count, total_bytes
        try:
            with os.scandir(p) as it:
                for entry in it:
                    if entry.is_file(follow_symlinks=False):
                        try:
                            stat = entry.stat(follow_symlinks=False)
                        except OSError:
                            continue
                        if stat.st_mtime < cutoff:
                            count += 1
                            total_bytes += stat.st_size
                    elif entry.is_dir(follow_symlinks=False):
                        _walk(entry.path)
        except PermissionError:
            pass

    if root.exists():
        _walk(str(root))
    return count, total_bytes


def _remove_empty_dirs(root: Path) -> None:
    """Remove empty leaf directories under root (bottom-up)."""
    if not root.exists():
        return
    for dirpath, dirnames, filenames in os.walk(str(root), topdown=False):
        if not filenames and not dirnames:
            p = Path(dirpath)
            if p != root:
                try:
                    p.rmdir()
                except OSError:
                    pass


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def cache_status(detail: bool = False) -> dict:
    """Scan cache directories and return usage statistics.

    Args:
        detail: If True, include per-subdirectory breakdown.

    Returns:
        Dict with status, categories (metadata, data_cache), total_bytes.
    """
    root = _get_cache_root()

    categories = {}
    for name in ("metadata", "data_cache", "validation"):
        cat_path = root / name
        stats = _scan_directory(cat_path)
        entry = {
            "path": stats["path"],
            "total_bytes": stats["total_bytes"],
            "total_human": stats["total_human"],
            "file_count": stats["file_count"],
        }
        if detail:
            entry["subcategories"] = _scan_subdirectories(cat_path)
        categories[name] = entry

    total = sum(c["total_bytes"] for c in categories.values())
    return {
        "status": "success",
        "categories": categories,
        "total_bytes": total,
        "total_human": _format_bytes(total),
    }


def cache_clean(
    category: str = "all",
    missions: list[str] | None = None,
    older_than_days: int | None = None,
    dry_run: bool = True,
) -> dict:
    """Delete cached files with optional filters.

    Args:
        category: "metadata", "data_cache", or "all".
        missions: Filter data cache to specific mission subdirectories.
        older_than_days: Only delete files older than N days.
        dry_run: If True (default), report what would be deleted without deleting.

    Returns:
        Dict with deleted_count, freed_bytes, freed_human, dry_run.
    """
    root = _get_cache_root()
    if category == "all":
        targets = ["metadata", "data_cache", "validation"]
    else:
        targets = [category]

    total_deleted = 0
    total_freed = 0

    for cat in targets:
        cat_path = root / cat

        if cat == "data_cache" and missions:
            for name in missions:
                _validate_name(name)
                target = cat_path / name
                if not target.exists():
                    continue
                if older_than_days is not None:
                    if dry_run:
                        c, b = _count_old_files(target, older_than_days)
                    else:
                        c, b = _delete_old_files(target, older_than_days)
                else:
                    stats = _scan_directory(target)
                    c = stats["file_count"]
                    b = stats["total_bytes"]
                    if not dry_run:
                        shutil.rmtree(target, ignore_errors=True)
                total_deleted += c
                total_freed += b
        elif older_than_days is not None:
            if not cat_path.exists():
                continue
            if dry_run:
                c, b = _count_old_files(cat_path, older_than_days)
            else:
                c, b = _delete_old_files(cat_path, older_than_days)
            total_deleted += c
            total_freed += b
        else:
            if not cat_path.exists():
                continue
            stats = _scan_directory(cat_path)
            total_deleted += stats["file_count"]
            total_freed += stats["total_bytes"]
            if not dry_run:
                shutil.rmtree(cat_path, ignore_errors=True)
                cat_path.mkdir(parents=True, exist_ok=True)

    return {
        "status": "success",
        "deleted_count": total_deleted,
        "freed_bytes": total_freed,
        "freed_human": _format_bytes(total_freed),
        "dry_run": dry_run,
    }


def _fetch_metadata_from_label(dataset_id: str) -> dict | None:
    """Download a PDS label and extract metadata. Delegates to metadata module."""
    from pdsmcp.metadata import _fetch_metadata_from_label as _impl
    return _impl(dataset_id)


def refresh_metadata(
    dataset_ids: list[str] | None = None,
    mission: str | None = None,
) -> dict:
    """Re-fetch parameter metadata from PDS label files.

    Args:
        dataset_ids: Specific dataset IDs to refresh.
        mission: Refresh all cached datasets belonging to this mission.

    Returns:
        Dict with refreshed count, failed count, details.
    """
    from pdsmcp.metadata import get_cache_dir, _dataset_id_to_cache_filename

    meta_dir = get_cache_dir()
    meta_dir.mkdir(parents=True, exist_ok=True)

    ids_to_refresh: list[str] = []
    if dataset_ids:
        ids_to_refresh = list(dataset_ids)
    elif mission:
        from pdsmcp.catalog import match_dataset_to_mission
        for f in meta_dir.glob("*.json"):
            # Reverse the cache filename to dataset ID
            ds_id = f.stem.replace("_", ":", 1)  # rough reverse
            ds_mission, _ = match_dataset_to_mission(ds_id)
            if ds_mission == mission:
                ids_to_refresh.append(ds_id)
        # Also scan mission JSON for all dataset IDs
        if not ids_to_refresh:
            from pdsmcp.catalog import load_mission_json
            try:
                mission_data = load_mission_json(mission)
                for inst in mission_data.get("instruments", {}).values():
                    for ds_id in inst.get("datasets", {}):
                        ids_to_refresh.append(ds_id)
            except FileNotFoundError:
                pass
    else:
        # Refresh all — collect dataset IDs from all mission JSONs
        from pdsmcp.catalog import get_missions_dir
        missions_dir = get_missions_dir()
        for filepath in sorted(missions_dir.glob("*.json")):
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    mission_data = json.load(f)
                for inst in mission_data.get("instruments", {}).values():
                    for ds_id in inst.get("datasets", {}):
                        cache_file = meta_dir / _dataset_id_to_cache_filename(ds_id)
                        if cache_file.exists():
                            ids_to_refresh.append(ds_id)
            except (json.JSONDecodeError, OSError):
                continue

    if not ids_to_refresh:
        return {"status": "success", "refreshed": 0, "failed": 0, "details": {}}

    refreshed = 0
    failed = 0
    details = {}

    for ds_id in ids_to_refresh:
        info = _fetch_metadata_from_label(ds_id)
        if info is None:
            failed += 1
            details[ds_id] = "failed"
            continue

        cache_file = meta_dir / _dataset_id_to_cache_filename(ds_id)
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump(info, f, indent=2)
        refreshed += 1
        details[ds_id] = "refreshed"

    return {
        "status": "success",
        "refreshed": refreshed,
        "failed": failed,
        "details": details,
    }


def refresh_time_ranges(mission: str | None = None) -> dict:
    """Update start/stop dates in bundled mission JSONs from Metadex API.

    Fetches the Metadex catalog (single HTTP call) and patches all mission
    JSON files with fresh time coverage.

    Args:
        mission: Only refresh this mission stem. If None, refresh all.

    Returns:
        Dict with missions_updated, datasets_updated, datasets_failed.
    """
    from pdsmcp.scripts.build_catalog import (
        fetch_all_ppi_collections,
        metadex_id_to_dataset_id,
    )

    missions_dir = _get_missions_dir()

    try:
        collections = fetch_all_ppi_collections()
    except Exception as e:
        return {
            "status": "error",
            "message": f"Could not fetch Metadex catalog: {e}",
            "missions_updated": 0,
            "datasets_updated": 0,
            "datasets_failed": 0,
        }

    # Build lookup: dataset_id → {start_date, stop_date}
    time_ranges: dict[str, dict] = {}
    for coll in collections:
        ds_id = metadex_id_to_dataset_id(coll["id"], coll["archive_type"])
        start = coll.get("start_date_time", "")
        stop = coll.get("stop_date_time", "")
        if start and "T" in start:
            start = start.split("T")[0]
        if stop and "T" in stop:
            stop = stop.split("T")[0]
        time_ranges[ds_id] = {"start_date": start, "stop_date": stop}

    if not time_ranges:
        return {
            "status": "error",
            "message": "Metadex returned no collections",
            "missions_updated": 0,
            "datasets_updated": 0,
            "datasets_failed": 0,
        }

    missions_updated = 0
    datasets_updated = 0
    datasets_failed = 0

    for filepath in sorted(missions_dir.glob("*.json")):
        stem = filepath.stem
        if mission and stem != mission:
            continue

        try:
            with open(filepath, "r", encoding="utf-8") as f:
                mission_data = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue

        stem_updated = 0
        for inst in mission_data.get("instruments", {}).values():
            for ds_id, ds_entry in inst.get("datasets", {}).items():
                meta = time_ranges.get(ds_id)
                if meta is None:
                    datasets_failed += 1
                    continue
                new_start = meta.get("start_date", "")
                new_stop = meta.get("stop_date", "")
                if new_start:
                    ds_entry["start_date"] = new_start
                if new_stop:
                    ds_entry["stop_date"] = new_stop
                stem_updated += 1

        if stem_updated > 0:
            mission_data.setdefault("_meta", {})
            mission_data["_meta"]["time_ranges_updated_at"] = (
                datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            )
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(mission_data, f, indent=2, ensure_ascii=False)
                f.write("\n")
            missions_updated += 1

        datasets_updated += stem_updated

    return {
        "status": "success",
        "missions_updated": missions_updated,
        "datasets_updated": datasets_updated,
        "datasets_failed": datasets_failed,
    }


def rebuild_catalog(mission: str | None = None) -> dict:
    """Rebuild mission catalog JSONs from Metadex API.

    Downloads the full Metadex dataset catalog and regenerates mission JSON
    files. This is the programmatic equivalent of:
        python -m pdsmcp.scripts.build_catalog [--mission <stem>]

    Args:
        mission: Only rebuild this mission stem. If None, rebuild all.

    Returns:
        Dict with missions_rebuilt count and details.
    """
    from pdsmcp.scripts.build_catalog import build_catalog as _build

    try:
        only_stems = {mission} if mission else None
        _build(only_stems=only_stems)
    except Exception as e:
        return {
            "status": "error",
            "message": f"Catalog rebuild failed: {e}",
            "missions_rebuilt": 0,
        }

    # Count what was rebuilt
    missions_dir = _get_missions_dir()
    rebuilt = []
    for filepath in sorted(missions_dir.glob("*.json")):
        if mission and filepath.stem != mission:
            continue
        rebuilt.append(filepath.stem)

    return {
        "status": "success",
        "missions_rebuilt": len(rebuilt),
        "missions": rebuilt,
    }
