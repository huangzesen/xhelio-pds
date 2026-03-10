#!/usr/bin/env python3
"""Build PDS PPI mission catalog JSONs from Metadex Solr API.

Queries the PDS PPI Metadex, groups collections by mission, and writes
one JSON per mission to ``src/pdsmcp/data/missions/``.

Usage:
    python -m pdsmcp.scripts.build_catalog                  # All missions
    python -m pdsmcp.scripts.build_catalog --mission juno    # One mission
    python -m pdsmcp.scripts.build_catalog --list            # List missions only
"""

import argparse
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

from pdsmcp.catalog import (
    MISSION_NAMES,
    MISSION_PREFIX_MAP,
    get_missions_dir,
    match_dataset_to_mission,
)
from pdsmcp.http import request_with_retry

logger = logging.getLogger(__name__)

METADEX_BASE = "https://pds-ppi.igpp.ucla.edu/metadex/collection/select/"

# Fields to request from Solr
_FIELDS = ",".join([
    "id",
    "title",
    "description",
    "bundle_id",
    "slot",
    "archive_type",
    "start_date_time",
    "stop_date_time",
    "observing_system.observing_system_component.name",
    "observing_system.observing_system_component.type",
    "target_identification.name",
    "investigation_area.name",
    "citation_information.doi",
])


# ---------------------------------------------------------------------------
# Metadex API
# ---------------------------------------------------------------------------

def fetch_all_ppi_collections(rows: int = 2000) -> list[dict]:
    """Fetch all data collections from PDS PPI Metadex (single HTTP call).

    Args:
        rows: Max rows to return (default 2000, well above current ~1,279).

    Returns:
        List of normalized collection dicts.
    """
    params = {
        "q": "*:*",
        "fq": "type:Data OR type:DATA OR type:data",
        "rows": rows,
        "wt": "json",
        "fl": _FIELDS,
    }

    resp = request_with_retry(METADEX_BASE, timeout=30, params=params)
    data = resp.json()
    docs = data.get("response", {}).get("docs", [])
    logger.info("Metadex returned %d data collections", len(docs))

    return [_normalize_doc(doc) for doc in docs]


def metadex_id_to_dataset_id(metadex_id: str, archive_type: int) -> str:
    """Convert a Metadex collection ID to a pdsmcp dataset ID.

    PDS4 URNs pass through as-is. PDS3 IDs get a ``pds3:`` prefix.
    """
    if archive_type == 4:
        return metadex_id
    return f"pds3:{metadex_id}"


def _normalize_doc(doc: dict) -> dict:
    """Normalize a Metadex Solr document to a flat dict."""
    component_names = doc.get(
        "observing_system.observing_system_component.name", []
    )
    component_types = doc.get(
        "observing_system.observing_system_component.type", []
    )

    instruments = []
    for i, name in enumerate(component_names):
        ctype = component_types[i] if i < len(component_types) else ""
        if ctype.lower() not in ("spacecraft", "host"):
            instruments.append(name)

    targets = doc.get("target_identification.name", [])
    if isinstance(targets, str):
        targets = [targets]

    doi_list = doc.get("citation_information.doi", [])
    doi = doi_list[0] if doi_list else ""

    return {
        "id": doc.get("id", ""),
        "title": doc.get("title", ""),
        "description": doc.get("description", ""),
        "bundle_id": doc.get("bundle_id", ""),
        "slot": doc.get("slot", ""),
        "archive_type": doc.get("archive_type", 0),
        "start_date_time": doc.get("start_date_time", ""),
        "stop_date_time": doc.get("stop_date_time", ""),
        "instruments": instruments,
        "targets": targets,
        "doi": doi,
    }


# ---------------------------------------------------------------------------
# Mission JSON building
# ---------------------------------------------------------------------------

# Map lowercase Metadex instrument names → clean group keys
_MAP_INSTRUMENT_NAME: dict[str, str] = {
    "magnetometer": "MAG",
    "fluxgate magnetometer": "MAG",
    "mag": "MAG",
    "waves": "Waves",
    "plasma wave instrument": "Waves",
    "plasma wave science": "Waves",
    "plasma wave subsystem": "Waves",
    "plasma wave spectrometer": "Waves",
    "radio and plasma wave investigation": "Waves",
    "jovian auroral distributions experiment": "JADE",
    "jupiter energetic particle detector instrument": "JEDI",
    "advanced stellar compass": "ASC",
    "plasma science": "Plasma",
    "plasma": "Plasma",
    "plasma instrument": "Plasma",
    "plasma analyzer": "Plasma",
    "solar wind electron analyzer": "Solar Wind",
    "solar wind ion analyzer": "Solar Wind",
    "solar wind around pluto": "Solar Wind",
    "solar energetic particle": "SEP",
    "cosmic ray subsystem": "Cosmic Ray",
    "low energy charged particle": "Energetic Particles",
    "energetic particles detector": "Energetic Particles",
    "extreme ultraviolet monitor": "EUV",
    "langmuir probe and waves": "LPW",
    "suprathermal and thermal ion composition": "STATIC",
}

_INSTRUMENT_KEYWORDS: dict[str, list[str]] = {
    "MAG": ["magnetic", "field", "mag", "magnetometer"],
    "Plasma": ["plasma", "ion", "electron", "density", "velocity"],
    "Waves": ["radio", "wave", "plasma wave"],
    "Cosmic Ray": ["particle", "energetic", "cosmic ray"],
    "Energetic Particles": ["particle", "energetic"],
    "Solar Wind": ["plasma", "solar wind", "ion"],
    "SEP": ["particle", "energetic"],
    "EUV": ["imaging", "remote sensing"],
    "LPW": ["electric", "e-field"],
    "STATIC": ["plasma", "ion"],
}


def _derive_instrument_key(
    dataset_id: str, title: str,
    instruments: list[str] | None = None,
) -> str:
    """Derive an instrument group key from Metadex data."""
    if instruments:
        for inst_name in instruments:
            mapped = _MAP_INSTRUMENT_NAME.get(inst_name.lower())
            if mapped:
                return mapped
        return instruments[0]

    raw_id = dataset_id.replace("urn:nasa:pds:", "").replace("pds3:", "")
    lower = raw_id.lower()
    title_lower = title.lower()

    if "fgm" in lower or ("mag" in lower and "image" not in lower):
        return "MAG"
    elif "pls" in lower or "plasma" in title_lower:
        return "Plasma"
    elif "pws" in lower or "radio" in title_lower or "wave" in title_lower:
        return "Waves"
    elif "crs" in lower or "cosmic" in title_lower:
        return "Cosmic Ray"
    elif "lecp" in lower or "energetic" in title_lower:
        return "Energetic Particles"
    elif "jad" in lower or "jade" in title_lower:
        return "JADE"
    elif "jed" in lower or "jedi" in title_lower:
        return "JEDI"
    elif "asc" in lower and "jno" in lower:
        return "ASC"
    elif "swea" in lower or "solar-wind" in lower:
        return "Solar Wind"
    elif "sep" in lower:
        return "SEP"
    elif "swia" in lower or "swi" in lower:
        return "Solar Wind"
    elif "euv" in lower:
        return "EUV"
    elif "lpw" in lower:
        return "LPW"
    elif "static" in lower:
        return "STATIC"

    return "General"


def _get_canonical_id(stem: str) -> str:
    """Return the canonical mission ID for a stem."""
    if "_" in stem:
        return stem.upper().replace("_", "-")
    return stem.upper()


def _build_mission_json(stem: str, collections: list[dict]) -> dict:
    """Build a PPI mission JSON from Metadex collection dicts."""
    name = MISSION_NAMES.get(stem, stem.upper())

    keywords = set()
    keywords.add(stem.lower())
    for word in name.split():
        w = word.strip("/()")
        if len(w) > 1:
            keywords.add(w.lower())
    keywords.add("ppi")
    keywords.add("pds")

    canonical_id = _get_canonical_id(stem)
    mission_id = canonical_id + "_PPI"
    mission_name = f"{name} (PDS PPI)"
    keywords.add(mission_id.lower().replace("-", "_"))

    mission = {
        "id": mission_id,
        "name": mission_name,
        "keywords": sorted(keywords),
        "profile": {
            "description": f"{name} data from PDS Planetary Plasma Interactions archive.",
            "coordinate_systems": [],
            "typical_cadence": "",
            "data_caveats": [
                "PDS3 datasets use fixed-width ASCII tables (.sts/.TAB files).",
                "PDS4 datasets use XML-labeled ASCII tables.",
            ],
            "analysis_patterns": [],
        },
        "instruments": {},
    }

    instrument_datasets: dict[str, dict] = {}

    for coll in collections:
        dataset_id = coll["_dataset_id"]
        title = coll.get("title", "")
        instruments = coll.get("instruments", [])

        inst_key = _derive_instrument_key(
            dataset_id, title, instruments=instruments,
        )

        start = coll.get("start_date_time", "")
        stop = coll.get("stop_date_time", "")
        if start and "T" in start:
            start = start.split("T")[0]
        if stop and "T" in stop:
            stop = stop.split("T")[0]

        ds_entry = {
            "description": title,
            "start_date": start,
            "stop_date": stop,
        }

        slot = coll.get("slot", "")
        if slot:
            ds_entry["slot"] = slot

        archive_type = coll.get("archive_type", 0)
        if archive_type:
            ds_entry["archive_type"] = archive_type

        instrument_datasets.setdefault(inst_key, {})[dataset_id] = ds_entry

    for inst_key, datasets in sorted(instrument_datasets.items()):
        mission["instruments"][inst_key] = {
            "name": inst_key,
            "keywords": _INSTRUMENT_KEYWORDS.get(inst_key, []),
            "datasets": datasets,
        }

    mission["_meta"] = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "PDS PPI Metadex",
    }

    return mission


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def build_catalog(only_stems: set[str] | None = None) -> None:
    """Fetch Metadex and write mission JSONs to the package data directory.

    Args:
        only_stems: If provided, only generate these mission stems.
    """
    start_time = time.time()

    logger.info("Fetching PPI collections from Metadex...")
    collections = fetch_all_ppi_collections()

    # Group by mission
    groups: dict[str, list[dict]] = {}
    for coll in collections:
        dataset_id = metadex_id_to_dataset_id(coll["id"], coll["archive_type"])
        coll["_dataset_id"] = dataset_id
        stem, _ = match_dataset_to_mission(dataset_id)
        if stem:
            groups.setdefault(stem, []).append(coll)

    if only_stems:
        groups = {s: ds for s, ds in groups.items() if s in only_stems}

    total_datasets = sum(len(ds) for ds in groups.values())
    logger.info(
        "Grouped %d datasets into %d missions", total_datasets, len(groups)
    )

    missions_dir = get_missions_dir()
    missions_dir.mkdir(parents=True, exist_ok=True)

    for i, stem in enumerate(sorted(groups)):
        stem_collections = groups[stem]
        logger.info(
            "Building %s (%d/%d): %d datasets",
            stem, i + 1, len(groups), len(stem_collections),
        )

        mission = _build_mission_json(stem, stem_collections)
        filepath = missions_dir / f"{stem}.json"
        filepath.write_text(
            json.dumps(mission, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    elapsed = time.time() - start_time
    logger.info(
        "Catalog build complete in %.0fs: %d missions, %d datasets",
        elapsed, len(groups), total_datasets,
    )


def list_missions() -> None:
    """Fetch Metadex and print available missions."""
    collections = fetch_all_ppi_collections()
    groups: dict[str, list] = {}
    for coll in collections:
        dataset_id = metadex_id_to_dataset_id(coll["id"], coll["archive_type"])
        stem, _ = match_dataset_to_mission(dataset_id)
        if stem:
            groups.setdefault(stem, []).append(coll)

    print(f"\nPPI missions ({len(groups)}) — {len(collections)} total collections:")
    for stem in sorted(groups):
        pds3 = sum(1 for c in groups[stem] if c["archive_type"] == 3)
        pds4 = sum(1 for c in groups[stem] if c["archive_type"] == 4)
        parts = []
        if pds3:
            parts.append(f"{pds3} PDS3")
        if pds4:
            parts.append(f"{pds4} PDS4")
        print(f"  {stem}: {len(groups[stem])} datasets ({', '.join(parts)})")


def main():
    parser = argparse.ArgumentParser(
        description="Build PDS PPI mission catalog from Metadex"
    )
    parser.add_argument(
        "--mission", type=str, default=None,
        help="Build only one mission (e.g., 'cassini')",
    )
    parser.add_argument(
        "--list", action="store_true",
        help="List missions from Metadex without building",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if args.list:
        list_missions()
        return

    only_stems = None
    if args.mission:
        only_stems = {args.mission.lower().replace("-", "_")}

    build_catalog(only_stems=only_stems)


if __name__ == "__main__":
    main()
