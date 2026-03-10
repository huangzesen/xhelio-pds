"""Parameter metadata — browse dataset variables via local cache or PDS labels.

Resolution chain: local JSON cache -> download one label file from the PDS
archive, parse it (PDS3 ODL or PDS4 XML), cache the result locally.

Cache directory: ``~/.pdsmcp/metadata/`` (configurable via ``PDSMCP_CACHE_DIR``).
"""

import json
import logging
import os
import re
import xml.etree.ElementTree as ET
from pathlib import Path

from pdsmcp.catalog import get_dataset_info
from pdsmcp.http import request_with_retry
from pdsmcp.label_parser import parse_pds3_label

logger = logging.getLogger(__name__)

_DEFAULT_CACHE_DIR = Path.home() / ".pdsmcp" / "metadata"

# PDS PPI archive base URL
PPI_ARCHIVE_BASE = "https://pds-ppi.igpp.ucla.edu"

# PDS4 XML namespace
_PDS4_NS = "http://pds.nasa.gov/pds4/pds/v1"

# Time-like column names to skip when building parameter metadata
_TIME_NAMES = frozenset({
    "time", "epoch", "utc", "scet", "datetime", "date_time",
    "timestamp", "sample utc",
})

# Data file extensions that indicate a table file has real data
_DATA_EXTS = frozenset({".tab", ".csv", ".dat", ".sts"})

# Label file extensions in preference order
_LABEL_EXTS = (".xml", ".lblx", ".lbl")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_cache_dir() -> Path:
    """Return the metadata cache directory.

    Configurable via the ``PDSMCP_CACHE_DIR`` environment variable.  When set,
    the metadata cache lives at ``$PDSMCP_CACHE_DIR/metadata/``.  Otherwise
    defaults to ``~/.pdsmcp/metadata/``.
    """
    custom = os.environ.get("PDSMCP_CACHE_DIR")
    if custom:
        return Path(custom) / "metadata"
    return _DEFAULT_CACHE_DIR


def browse_parameters(
    dataset_id: str | None = None,
    dataset_ids: list[str] | None = None,
) -> dict:
    """Browse parameters for one or more PDS datasets.

    Resolution chain:
    1. Local metadata cache (``~/.pdsmcp/metadata/{safe_id}.json``)
    2. Download one label file from the PDS archive, parse it, cache result

    Args:
        dataset_id: Single dataset ID (PDS4 URN or ``pds3:`` prefixed).
        dataset_ids: Multiple dataset IDs for batch lookup.

    Returns:
        Dict with ``status`` and parameter metadata.  For a single dataset the
        result is flattened; for multiple datasets a ``datasets`` dict is
        returned keyed by dataset ID.
    """
    ids: list[str] = []
    if dataset_ids:
        ids = dataset_ids
    elif dataset_id:
        ids = [dataset_id]

    if not ids:
        return {
            "status": "error",
            "message": "Missing required parameter: dataset_id or dataset_ids",
        }

    results: dict[str, dict] = {}
    for ds_id in ids:
        try:
            info = _resolve_metadata(ds_id)
            # Exclude the leading Time pseudo-parameter from the listing
            params = [
                p for p in info.get("parameters", [])
                if p.get("name", "").lower() != "time"
            ]
            entry: dict = {"parameters": params}
            start = info.get("startDate", "")
            stop = info.get("stopDate", "")
            if start or stop:
                entry["time_range"] = {"start": start, "stop": stop}
        except Exception as e:
            logger.warning("Could not load parameters for %s: %s", ds_id, e)
            entry = {"parameters": [], "error": str(e)}
        results[ds_id] = entry

    # Flatten for single-dataset calls
    if len(results) == 1:
        ds_id, entry = next(iter(results.items()))
        return {"status": "success", "dataset_id": ds_id, **entry}

    return {"status": "success", "datasets": results}


# ---------------------------------------------------------------------------
# Resolution chain
# ---------------------------------------------------------------------------

def _resolve_metadata(dataset_id: str) -> dict:
    """Resolve parameter metadata: local cache first, then label download.

    Side effect: caches the result locally after a successful label download
    and parse.

    Args:
        dataset_id: PDS dataset ID.

    Returns:
        Metadata dict with ``parameters`` list.

    Raises:
        RuntimeError: If metadata cannot be resolved from any source.
    """
    cache_dir = get_cache_dir()
    cache_filename = _dataset_id_to_cache_filename(dataset_id)
    cache_file = cache_dir / cache_filename

    # Try local cache
    if cache_file.exists():
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass

    # Fallback: download a label from the PDS archive
    info = _fetch_metadata_from_label(dataset_id)
    if info is None:
        raise RuntimeError(f"Could not fetch metadata for {dataset_id}")

    # Cache the result
    cache_dir.mkdir(parents=True, exist_ok=True)
    try:
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump(info, f, indent=2, ensure_ascii=False)
    except OSError as e:
        logger.warning("Failed to write metadata cache for %s: %s", dataset_id, e)

    return info


def _dataset_id_to_cache_filename(dataset_id: str) -> str:
    """Sanitize a dataset ID to a safe cache filename.

    Replaces ``:`` and ``/`` with ``_`` and appends ``.json``.

    Examples:
        ``urn:nasa:pds:cassini-mag-cal:data-1sec-krtp``
        -> ``urn_nasa_pds_cassini-mag-cal_data-1sec-krtp.json``

        ``pds3:JNO-J-3-FGM-CAL-V1.0:DATA``
        -> ``pds3_JNO-J-3-FGM-CAL-V1.0_DATA.json``
    """
    safe_id = dataset_id.replace(":", "_").replace("/", "_")
    return f"{safe_id}.json"


# ---------------------------------------------------------------------------
# Label fetching and parsing
# ---------------------------------------------------------------------------

def _fetch_metadata_from_label(dataset_id: str) -> dict | None:
    """Download one label file from the dataset's archive dir, parse it.

    Uses ``get_dataset_info`` to look up the ``slot`` field (archive path)
    for the dataset, then BFS-searches the archive directory for a label file
    with a matching data file.

    Args:
        dataset_id: PDS dataset ID.

    Returns:
        Metadata dict with ``parameters`` list, or None if no label found
        or the label has no useful numeric parameters.
    """
    # Resolve the collection URL from catalog slot
    collection_url = _resolve_collection_url_for_metadata(dataset_id)
    if collection_url is None:
        logger.warning("No archive URL resolved for %s", dataset_id)
        return None

    logger.info("Searching for label in %s", collection_url)

    result = _find_one_label(collection_url)
    if result is None:
        logger.warning("No label file found for %s at %s", dataset_id, collection_url)
        return None

    label_text, ext = result

    try:
        label = _parse_label_text(label_text, ext)
    except Exception as e:
        logger.warning("Failed to parse label for %s: %s", dataset_id, e)
        return None

    return _build_metadata_from_label(label)


def _resolve_collection_url_for_metadata(dataset_id: str) -> str | None:
    """Resolve a dataset ID to its PDS archive collection URL.

    Tries the catalog ``slot`` field first.  Falls back to constructing
    the URL from the dataset ID format.

    Args:
        dataset_id: PDS dataset ID.

    Returns:
        Full HTTPS URL to the collection directory (with trailing slash),
        or None if resolution fails.
    """
    # Try slot from catalog
    ds_info = get_dataset_info(dataset_id)
    if ds_info and ds_info.get("slot"):
        slot = ds_info["slot"]
        return f"{PPI_ARCHIVE_BASE}{slot}/"

    # Fallback: construct from dataset ID format
    if dataset_id.startswith("pds3:"):
        raw_id = dataset_id[len("pds3:"):]
        if ":" in raw_id:
            bundle_id, collection = raw_id.rsplit(":", 1)
        else:
            bundle_id = raw_id
            collection = "DATA"
        bundle_path = bundle_id.replace("/", "_")
        return f"{PPI_ARCHIVE_BASE}/data/{bundle_path}/{collection}/"

    if dataset_id.startswith("urn:nasa:pds:"):
        parts = dataset_id.split(":")
        if len(parts) >= 5:
            bundle_urn = parts[3]
            collection_urn = parts[4]
            bundle_path = bundle_urn.replace("_", "-")
            return f"{PPI_ARCHIVE_BASE}/data/{bundle_path}/{collection_urn}/"

    return None


def _find_one_label(
    collection_url: str,
    _depth: int = 0,
    _max_depth: int = 4,
) -> tuple[str, str] | None:
    """BFS to find one label file with a matching data file.

    At each directory level, looks for a file stem that has both a data
    extension (``.tab``, ``.csv``, ``.dat``, ``.sts``) and a label extension
    (``.xml``, ``.lblx``, ``.lbl``).  Skips files starting with "collection".
    If no match found, recurses into the first subdirectory.

    Args:
        collection_url: URL of a PDS archive directory.
        _depth: Current recursion depth.
        _max_depth: Maximum recursion depth.

    Returns:
        ``(label_text, extension)`` tuple where *extension* includes the dot
        (e.g. ``".xml"``), or None if no suitable label is found.
    """
    if _depth >= _max_depth:
        return None

    try:
        entries = _parse_html_listing(_fetch_directory_listing(collection_url))
    except Exception:
        return None

    # Separate files and directories
    files: dict[str, dict[str, str]] = {}  # stem_lower -> {ext_lower: original_name}
    subdirs: list[str] = []

    for e in sorted(entries, key=lambda x: x["name"]):
        name = e["name"]
        if e["is_dir"]:
            subdirs.append(name.rstrip("/"))
            continue

        p = Path(name)
        stem_lower = p.stem.lower()
        ext_lower = p.suffix.lower()

        # Skip PDS inventory/collection files
        if stem_lower.startswith("collection"):
            continue

        files.setdefault(stem_lower, {})[ext_lower] = name

    # Look for a label file with a matching data file
    for stem_lower in sorted(files):
        exts = files[stem_lower]
        has_data = any(ext in exts for ext in _DATA_EXTS)
        if not has_data:
            continue
        for label_ext in _LABEL_EXTS:
            if label_ext in exts:
                label_url = f"{collection_url}{exts[label_ext]}"
                try:
                    resp = request_with_retry(label_url, timeout=30)
                    return (resp.text, label_ext)
                except Exception:
                    continue

    # No match at this level -- recurse into first subdirectory
    for d in subdirs:
        result = _find_one_label(
            f"{collection_url}{d}/",
            _depth=_depth + 1,
            _max_depth=_max_depth,
        )
        if result is not None:
            return result

    return None


def _fetch_directory_listing(url: str) -> str:
    """Fetch an Apache directory index page.

    Args:
        url: URL of the directory.

    Returns:
        Raw HTML text of the listing page.
    """
    resp = request_with_retry(url, timeout=30)
    return resp.text


def _parse_html_listing(html: str) -> list[dict]:
    """Parse an Apache-style HTML directory listing.

    Matches ``<a href="name">`` links.  Skips parent directory (``../``),
    root (``/``), and sorting query links (``?...``).

    Args:
        html: Raw HTML text from an Apache directory index.

    Returns:
        List of dicts with ``name`` (str) and ``is_dir`` (bool) keys.
    """
    entries = []
    for m in re.finditer(r'<a\s+href="([^"?]+)"', html, re.IGNORECASE):
        name = m.group(1)
        # Skip parent dir, root, and query/sorting links
        if name in ("../", "/", "?") or name.startswith("?") or name.startswith("/"):
            continue
        is_dir = name.endswith("/")
        entries.append({"name": name, "is_dir": is_dir})
    return entries


# ---------------------------------------------------------------------------
# Label parsing dispatch
# ---------------------------------------------------------------------------

def _parse_label_text(text: str, ext: str) -> dict:
    """Dispatch to PDS3 or PDS4 parser based on label file extension.

    Args:
        text: Full text content of the label file.
        ext: File extension including dot (e.g. ``".lbl"``, ``".xml"``).

    Returns:
        Parsed label dict with ``fields``, ``table_type``, etc.
    """
    if ext.lower() == ".lbl":
        return parse_pds3_label(text)
    else:
        return _parse_xml_label(text)


def _parse_xml_label(xml_text: str) -> dict:
    """Parse a PDS4 XML label to extract table format information.

    Supports ``Table_Delimited`` and ``Table_Character`` (fixed-width) table
    types.  Falls back to no-namespace search for older labels.

    Args:
        xml_text: Full XML text of the label file.

    Returns:
        Dict with keys:
        - ``table_type``: ``"fixed_width"`` or ``"delimited"``
        - ``fields``: list of column dicts
        - ``delimiter``: delimiter character (for delimited) or None
        - ``records``: number of data rows (if available)

    Raises:
        ValueError: If no supported table element is found.
    """
    root = ET.fromstring(xml_text)

    # Try Table_Delimited first, then Table_Character
    table_delim = root.find(f".//{{{_PDS4_NS}}}Table_Delimited")
    if table_delim is not None:
        return _parse_delimited_label(table_delim)

    table_char = root.find(f".//{{{_PDS4_NS}}}Table_Character")
    if table_char is not None:
        return _parse_fixed_width_label(table_char)

    # Check for Table_Binary (not supported)
    table_bin = root.find(f".//{{{_PDS4_NS}}}Table_Binary")
    if table_bin is not None:
        raise ValueError(
            "Table_Binary format not yet supported. "
            "This dataset uses binary tables which require a different parser."
        )

    # Fallback: try without namespace (some older labels)
    table_delim = root.find(".//Table_Delimited")
    if table_delim is not None:
        return _parse_delimited_label(table_delim)

    table_char = root.find(".//Table_Character")
    if table_char is not None:
        return _parse_fixed_width_label(table_char)

    # Collect table-like element names for debugging
    table_tags = [
        child.tag.split("}")[-1] if "}" in child.tag else child.tag
        for child in root.iter()
        if "table" in (
            child.tag.split("}")[-1] if "}" in child.tag else child.tag
        ).lower()
    ]
    raise ValueError(
        f"No Table_Delimited or Table_Character found in XML label. "
        f"Table-like elements found: {table_tags or 'none'}"
    )


def _parse_delimited_label(table_elem) -> dict:
    """Parse a ``Table_Delimited`` element from a PDS4 XML label.

    Args:
        table_elem: An ElementTree element for ``Table_Delimited``.

    Returns:
        Parsed label dict with ``table_type="delimited"``.
    """
    ns = _PDS4_NS

    # Get delimiter
    delimiter = ","
    delim_elem = table_elem.find(f"{{{ns}}}field_delimiter")
    if delim_elem is not None:
        delim_text = (delim_elem.text or "").strip().lower()
        if delim_text in ("semicolon", "semi colon"):
            delimiter = ";"
        elif delim_text in ("comma",):
            delimiter = ","
        elif delim_text in ("tab", "horizontal tab"):
            delimiter = "\t"

    records_elem = table_elem.find(f"{{{ns}}}records")
    records = int(records_elem.text) if records_elem is not None else None

    # Parse fields from Record_Delimited
    fields = []
    record = table_elem.find(f"{{{ns}}}Record_Delimited")
    if record is not None:
        for i, field in enumerate(record.findall(f"{{{ns}}}Field_Delimited")):
            name_elem = field.find(f"{{{ns}}}name")
            fn_elem = field.find(f"{{{ns}}}field_number")
            unit_elem = field.find(f"{{{ns}}}unit")
            desc_elem = field.find(f"{{{ns}}}description")

            name = name_elem.text.strip() if name_elem is not None else f"col_{i}"
            field_number = int(fn_elem.text) if fn_elem is not None else i + 1
            unit = (
                unit_elem.text.strip()
                if unit_elem is not None and unit_elem.text
                else ""
            )
            description = (
                desc_elem.text.strip()
                if desc_elem is not None and desc_elem.text
                else ""
            )

            # Check for fill values in Special_Constants
            fill = _extract_special_constants(field, ns)

            entry = {
                "name": name,
                "field_number": field_number,
                "type": "delimited",
                "unit": unit,
                "description": description,
            }
            if fill is not None:
                entry["null_constant"] = fill
            fields.append(entry)

    return {
        "table_type": "delimited",
        "fields": fields,
        "delimiter": delimiter,
        "records": records,
    }


def _parse_fixed_width_label(table_elem) -> dict:
    """Parse a ``Table_Character`` (fixed-width) element from a PDS4 XML label.

    Args:
        table_elem: An ElementTree element for ``Table_Character``.

    Returns:
        Parsed label dict with ``table_type="fixed_width"``.
    """
    ns = _PDS4_NS

    records_elem = table_elem.find(f"{{{ns}}}records")
    records = int(records_elem.text) if records_elem is not None else None

    fields = []
    record = table_elem.find(f"{{{ns}}}Record_Character")
    if record is not None:
        for i, field in enumerate(record.findall(f"{{{ns}}}Field_Character")):
            name_elem = field.find(f"{{{ns}}}name")
            loc_elem = field.find(f"{{{ns}}}field_location")
            len_elem = field.find(f"{{{ns}}}field_length")
            unit_elem = field.find(f"{{{ns}}}unit")
            desc_elem = field.find(f"{{{ns}}}description")

            name = name_elem.text.strip() if name_elem is not None else f"col_{i}"

            # field_location can be a direct value or have a nested <offset> child
            offset = 0
            if loc_elem is not None:
                offset_child = loc_elem.find(f"{{{ns}}}offset")
                if offset_child is not None:
                    offset = int(offset_child.text)
                elif loc_elem.text and loc_elem.text.strip():
                    offset = int(loc_elem.text.strip())

            length = 0
            if len_elem is not None:
                length_child = len_elem.find(f"{{{ns}}}length")
                if length_child is not None:
                    length = int(length_child.text)
                elif len_elem.text and len_elem.text.strip():
                    length = int(len_elem.text.strip())

            unit = (
                unit_elem.text.strip()
                if unit_elem is not None and unit_elem.text
                else ""
            )
            description = (
                desc_elem.text.strip()
                if desc_elem is not None and desc_elem.text
                else ""
            )

            # Check for fill values in Special_Constants
            fill = _extract_special_constants(field, ns)

            entry = {
                "name": name,
                "offset": offset,
                "length": length,
                "type": "fixed_width",
                "unit": unit,
                "description": description,
            }
            if fill is not None:
                entry["null_constant"] = fill
            fields.append(entry)

    return {
        "table_type": "fixed_width",
        "fields": fields,
        "delimiter": None,
        "records": records,
    }


def _extract_special_constants(field_elem, ns: str) -> str | None:
    """Extract fill value from a ``Special_Constants`` child element.

    Checks in order: ``missing_constant``, ``missing_flag``,
    ``saturated_constant``, ``null_constant``.

    Args:
        field_elem: An ElementTree element for a field.
        ns: PDS4 XML namespace string.

    Returns:
        Fill value string, or None if no special constant is present.
    """
    sc = field_elem.find(f"{{{ns}}}Special_Constants")
    if sc is None:
        return None

    for tag in ("missing_constant", "missing_flag", "saturated_constant",
                "null_constant"):
        elem = sc.find(f"{{{ns}}}{tag}")
        if elem is not None and elem.text:
            return elem.text.strip()

    return None


# ---------------------------------------------------------------------------
# Metadata construction
# ---------------------------------------------------------------------------

def _build_metadata_from_label(label: dict) -> dict | None:
    """Build a metadata dict from a parsed label.

    Pure function -- no I/O.  Creates a parameter list starting with a Time
    pseudo-parameter, then adds numeric columns from the label.  Skips
    time-like columns and non-numeric types (CHAR, TIME, DATE).

    Args:
        label: Parsed label dict from ``_parse_label_text``.

    Returns:
        Metadata dict in the standard cache format with ``parameters``,
        ``description``, ``startDate``, ``stopDate``, and ``_meta`` keys.
        Returns None if the label has no useful numeric parameters (only
        Time).
    """
    parameters = [{"name": "Time", "type": "isotime", "length": 24}]

    for field in label.get("fields", []):
        fname = field.get("name", "")
        if fname.lower().strip() in _TIME_NAMES:
            continue

        ptype = field.get("type", "").upper()
        param_type = "double"
        if "INT" in ptype:
            param_type = "integer"
        elif "CHAR" in ptype or "TIME" in ptype or "DATE" in ptype:
            continue  # Skip non-numeric columns

        param: dict = {
            "name": fname,
            "type": param_type,
            "units": field.get("unit", ""),
            "description": field.get("description", ""),
            "size": [1],
        }
        null_const = field.get("null_constant")
        if null_const:
            param["fill"] = null_const
        parameters.append(param)

    if len(parameters) <= 1:
        return None  # Only Time -- nothing useful

    return {
        "parameters": parameters,
        "description": "",
        "startDate": "",
        "stopDate": "",
        "_meta": {"source": "label"},
    }
