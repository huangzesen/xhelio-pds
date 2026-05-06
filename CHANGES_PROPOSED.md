# CHANGES_PROPOSED — Bug Fixes from xhelio-skill-architect Testing

## Bug 1: Juno PDS3 Discovery Timeout

**Problem**: `_discover_recursive` scans ALL subdirectories without time-based filtering. For Juno FGM data (`JNO-J-3-FGM-CAL-V1.0`), this means visiting all 78 `PERI-XX` orbit directories even when the user requests a single day.

**Root cause**: The `_discover_recursive` function at depth ≥ 2 with many subdirectories uses parallel HTTP requests but visits every directory. Since `PERI-XX` directory names contain no date information, there's no way to filter them before visiting.

**Fix**: Added `_filter_dirs_by_peek_date()` function that:
1. Fetches each subdirectory's file listing in parallel (12 workers)
2. Extracts dates from the first 10 filenames using existing `_FILENAME_TIME_RE` and `_PDS3_FILENAME_DOY_RE` patterns
3. Filters directories whose date range (±60 day buffer) overlaps the requested time range
4. Falls back to including directories where no date can be extracted (fail-open)

Also lowered the parallelism threshold from `_depth >= 2` to `_depth >= 1` to activate filtering earlier in the recursion tree.

**Performance**: Juno 1-day request went from timeout (>30s) to 7.7 seconds (32 file pairs found). The 78 PERI directories are reduced to ~3 relevant ones.

**Files changed**: `src/pdsmcp/fetch.py` — added `_filter_dirs_by_peek_date`, modified `_discover_recursive`.

**Risk**: Low. The 60-day buffer is generous; fail-open for undatable directories means no data loss. The only behavioral change is skipping directories that provably contain only files outside the requested range.

---

## Bug 2: Voyager URN Resolution 404

**Problem**: PDS4 URNs like `urn:nasa:pds:vg1-mag-jup:data-hg-1.92s` fail with 404 because:
- Bundle name `vg1-mag-jup` doesn't match the archive directory `vg1-mag-jupiter`
- Collection name `data-hg-1.92s` doesn't match the archive directory `data-hgcoords-1_92sec`

**Root cause**: Two layers of naming mismatch:
1. **Bundle level**: URN abbreviates planet names (`jup`), archive uses full names (`jupiter`)
2. **Collection level**: URN uses short form (`data-hg-1.92s`), archive uses descriptive form (`data-hgcoords-1_92sec`)

**Fix**: Two-part solution:
1. **Slot-based resolution**: For PDS4 URNs, look up the `slot` field in the mission JSON first (via `_get_pds3_slot`). The slot contains the correct archive path pre-computed from the Metadex API. This handles ALL naming mismatches in one step.
2. **Planet abbreviation fallback**: Added `_expand_planet_abbreviation()` for the directory-based fallback path, expanding `jup`→`jupiter`, `sat`→`saturn`, etc. in bundle names.

**Result**: `urn:nasa:pds:vg1-mag-jup:data-hg-1.92s` → `https://pds-ppi.igpp.ucla.edu/data/vg1-mag-jupiter/data-hgcoords-1_92sec/`

**Files changed**: `src/pdsmcp/fetch.py` — added `_expand_planet_abbreviation`, modified `_resolve_pds4_collection_url` and `_match_collection`.

**Risk**: Low. Slot-based resolution is a fast path (local JSON lookup, no HTTP). Falls back to existing directory-based resolution when no slot exists. Planet abbreviation expansion only fires when the standard matching fails.

---

## Tests Added

`tests/test_bugfixes.py` — 10 tests for planet abbreviation expansion in `_match_collection`:
- `jup`→`jupiter`, `sat`→`saturn` expansion
- Exact match, hyphen/underscore swap, normalized match (backward compat)
- Underscore directory variants
- No false expansion on non-planet abbreviations
- No-match returns None
- Pre-existing URN format still works (`vg1-crs-jup-avg-flux`)

All 29 tests pass (10 new + 19 existing).
