"""Mission catalog — load bundled mission JSONs and generate summaries."""

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Package data directory
_PACKAGE_DATA = Path(__file__).parent / "data"

# PDS PPI prefix → (mission_stem, instrument_hint)
# Ordered most-specific first so that longer prefixes match before shorter ones.
MISSION_PREFIX_MAP: dict[str, tuple[str, str | None]] = {
    # PDS4 URN prefixes
    "urn:nasa:pds:cassini-": ("cassini", None),
    "urn:nasa:pds:voyager1.": ("voyager1", None),
    "urn:nasa:pds:voyager2.": ("voyager2", None),
    "urn:nasa:pds:voyager-pws-": ("voyager1", None),
    "urn:nasa:pds:vg1-": ("voyager1", None),
    "urn:nasa:pds:vg2-": ("voyager2", None),
    "urn:nasa:pds:juno": ("juno", None),
    "urn:nasa:pds:maven.": ("maven", None),
    "urn:nasa:pds:galileo-": ("galileo", None),
    "urn:nasa:pds:go-pls-": ("galileo", None),
    "urn:nasa:pds:p10-": ("pioneer", None),
    "urn:nasa:pds:p11-": ("pioneer", None),
    "urn:nasa:pds:ulysses-": ("ulysses", None),
    "urn:nasa:pds:mess-": ("messenger", None),
    "urn:nasa:pds:pvo-": ("pioneer_venus", None),
    "urn:nasa:pds:mgs-": ("mgs", None),
    "urn:nasa:pds:vex-": ("vex", None),
    "urn:nasa:pds:insight-": ("insight", None),
    "urn:nasa:pds:lp-": ("lunar_prospector", None),
    "urn:nasa:pds:lro-": ("lro", None),
    # PDS3 prefixes
    "pds3:JNO-": ("juno", None),
    "pds3:CO-": ("cassini", None),
    "pds3:VG1-": ("voyager1", None),
    "pds3:VG2-": ("voyager2", None),
    "pds3:ULY-": ("ulysses", None),
    "pds3:GO-": ("galileo", None),
    "pds3:MESS-": ("messenger", None),
    "pds3:PVO-": ("pioneer_venus", None),
    "pds3:MGS-": ("mgs", None),
    "pds3:VEX-": ("vex", None),
    "pds3:P10-": ("pioneer", None),
    "pds3:P11-": ("pioneer", None),
    "pds3:MEX-": ("mex", None),
    "pds3:NH-": ("new_horizons", None),
}

# Human-readable names for PDS PPI missions.
MISSION_NAMES: dict[str, str] = {
    "cassini": "Cassini",
    "galileo": "Galileo",
    "insight": "InSight",
    "juno": "Juno",
    "lro": "Lunar Reconnaissance Orbiter",
    "lunar_prospector": "Lunar Prospector",
    "maven": "MAVEN",
    "messenger": "MESSENGER",
    "mex": "Mars Express",
    "mgs": "Mars Global Surveyor",
    "new_horizons": "New Horizons",
    "pioneer": "Pioneer",
    "pioneer_venus": "Pioneer Venus",
    "ulysses": "Ulysses",
    "vex": "Venus Express",
    "voyager1": "Voyager 1",
    "voyager2": "Voyager 2",
}


def get_missions_dir() -> Path:
    """Return the path to the bundled missions directory."""
    return _PACKAGE_DATA / "missions"


def load_mission_json(mission_stem: str) -> dict:
    """Load a mission JSON file by stem name (e.g., 'juno', 'cassini').

    Args:
        mission_stem: Lowercase mission identifier.

    Returns:
        Parsed mission dict.

    Raises:
        FileNotFoundError: If no JSON file exists for this mission.
    """
    filepath = get_missions_dir() / f"{mission_stem}.json"
    if not filepath.exists():
        raise FileNotFoundError(f"Mission file not found: {filepath}")
    with open(filepath, "r", encoding="utf-8") as f:
        return json.load(f)


def browse_missions(query: str | None = None) -> list[dict]:
    """List all available PDS PPI missions with summaries.

    Args:
        query: Optional keyword filter (case-insensitive substring match
               against mission name, description, and instrument names).

    Returns:
        List of dicts with: id, name, description, dataset_count, instruments.
    """
    missions_dir = get_missions_dir()
    if not missions_dir.exists():
        return []

    results = []
    for filepath in sorted(missions_dir.glob("*.json")):
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                mission = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Failed to load %s: %s", filepath, e)
            continue

        dataset_count = sum(
            len(inst.get("datasets", {}))
            for inst in mission.get("instruments", {}).values()
        )

        profile = mission.get("profile", {})
        instruments = list(mission.get("instruments", {}).keys())

        entry = {
            "id": mission.get("id", filepath.stem.upper()),
            "name": mission.get("name", filepath.stem),
            "description": profile.get("description", ""),
            "dataset_count": dataset_count,
            "instruments": instruments,
        }

        # Apply keyword filter if provided
        if query:
            q = query.lower()
            searchable = " ".join([
                entry["name"].lower(),
                entry["description"].lower(),
                " ".join(instruments).lower(),
            ])
            if q not in searchable:
                continue

        results.append(entry)

    return results


def mission_to_markdown(mission: dict) -> str:
    """Convert a mission JSON dict to a readable markdown dataset catalog.

    Args:
        mission: Full mission dict from load_mission_json().

    Returns:
        Markdown string with dataset catalog.
    """
    lines = ["## Dataset Catalog", ""]
    for inst_name, inst_data in sorted(mission.get("instruments", {}).items()):
        lines.append(f"### {inst_name}")
        if inst_data.get("keywords"):
            lines.append(f"Keywords: {', '.join(inst_data['keywords'])}")
        lines.append("")
        for ds_id, ds_info in sorted(inst_data.get("datasets", {}).items()):
            desc = ds_info.get("description", "")
            start = ds_info.get("start_date", "?")
            stop = ds_info.get("stop_date", "?")
            archive_type = ds_info.get("archive_type", "")
            lines.append(f"- **{ds_id}**: {desc}")
            lines.append(f"  Coverage: {start} to {stop}")
            if archive_type:
                lines.append(f"  Archive: PDS{archive_type}")
        lines.append("")
    return "\n".join(lines)


def match_dataset_to_mission(dataset_id: str) -> tuple[str | None, str | None]:
    """Map a PDS dataset ID to (mission_stem, instrument_hint).

    Checks prefixes from most specific to least specific.

    Args:
        dataset_id: PDS dataset ID (URN or pds3: prefixed).

    Returns:
        (mission_stem, instrument_hint) or (None, None) if no match.
    """
    for prefix, (mission, instrument) in MISSION_PREFIX_MAP.items():
        if dataset_id.startswith(prefix):
            return mission, instrument
    return None, None


def get_mission_stem_from_dataset(dataset_id: str) -> str | None:
    """Find which mission a dataset belongs to.

    First tries prefix matching (fast), then falls back to scanning
    all mission JSONs (slower but handles edge cases).

    Args:
        dataset_id: PDS dataset ID.

    Returns:
        Mission stem (e.g., 'juno') or None.
    """
    # Fast path: prefix matching
    stem, _ = match_dataset_to_mission(dataset_id)
    if stem:
        return stem

    # Slow path: scan all mission JSONs
    missions_dir = get_missions_dir()
    if not missions_dir.exists():
        return None

    for filepath in missions_dir.glob("*.json"):
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                mission = json.load(f)
            for inst in mission.get("instruments", {}).values():
                if dataset_id in inst.get("datasets", {}):
                    return filepath.stem
        except (json.JSONDecodeError, OSError):
            continue
    return None


def get_dataset_info(dataset_id: str) -> dict | None:
    """Look up a dataset's metadata from the bundled mission JSONs.

    Args:
        dataset_id: PDS dataset ID.

    Returns:
        Dataset info dict (description, start_date, stop_date, slot,
        archive_type) or None if not found.
    """
    stem = get_mission_stem_from_dataset(dataset_id)
    if not stem:
        return None
    try:
        mission = load_mission_json(stem)
    except FileNotFoundError:
        return None
    for inst in mission.get("instruments", {}).values():
        if dataset_id in inst.get("datasets", {}):
            return inst["datasets"][dataset_id]
    return None
