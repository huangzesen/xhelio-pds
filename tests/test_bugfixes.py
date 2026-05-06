"""Tests for PDS bug fixes.

Bug 1: Juno PDS3 discovery timeout — 78 orbit dirs scanned without time filtering
Bug 2: Voyager URN resolution — vg1-mag-jup doesn't resolve to vg1-mag-jupiter
"""

from pdsmcp.fetch import _match_collection


class TestMatchCollectionPlanetExpansion:
    """Test planet abbreviation expansion in _match_collection (Bug 2)."""

    def test_jup_expands_to_jupiter(self):
        """vg1-mag-jup should match vg1-mag-jupiter."""
        dirs = ["vg1-mag-jupiter", "vg1-mag-saturn"]
        assert _match_collection("vg1-mag-jup", dirs) == "vg1-mag-jupiter"

    def test_sat_expands_to_saturn(self):
        """vg1-mag-sat should match vg1-mag-saturn."""
        dirs = ["vg1-mag-jupiter", "vg1-mag-saturn"]
        assert _match_collection("vg1-mag-sat", dirs) == "vg1-mag-saturn"

    def test_exact_match_still_works(self):
        """Exact matches should still work."""
        dirs = ["data-1sec-krtp", "data-1sec-krtn"]
        assert _match_collection("data-1sec-krtp", dirs) == "data-1sec-krtp"

    def test_hyphen_underscore_swap_still_works(self):
        """Hyphen/underscore swaps should still work."""
        dirs = ["data_1sec_krtp"]
        assert _match_collection("data-1sec-krtp", dirs) == "data_1sec_krtp"

    def test_normalized_match_still_works(self):
        """Normalized comparison should still work."""
        dirs = ["Data-1sec-KRTP"]
        assert _match_collection("data-1sec-krtp", dirs) == "Data-1sec-KRTP"

    def test_expansion_with_underscore_dir(self):
        """Planet expansion should also match underscore variants."""
        dirs = ["vg1_mag_jupiter"]
        assert _match_collection("vg1-mag-jup", dirs) == "vg1_mag_jupiter"

    def test_expansion_matches_correctly(self):
        """Planet expansion should match when the expanded name exists."""
        dirs = ["vg1-something-jupiter"]
        # 'jup' expands to 'jupiter', so this should match
        assert _match_collection("vg1-something-jup", dirs) == "vg1-something-jupiter"

    def test_no_expansion_when_not_planet(self):
        """Should not expand abbreviations that aren't planet names."""
        dirs = ["vg1-something-foobar"]
        assert _match_collection("vg1-something-foo", dirs) is None

    def test_no_match_returns_none(self):
        """Should return None when no match is found."""
        dirs = ["completely-different"]
        assert _match_collection("vg1-mag-jup", dirs) is None

    def test_crs_jup_bundle_still_works(self):
        """vg1-crs-jup-avg-flux should match if it exists as-is (no expansion needed)."""
        dirs = ["vg1-crs-jup-avg-flux", "vg1-mag-jupiter"]
        assert _match_collection("vg1-crs-jup-avg-flux", dirs) == "vg1-crs-jup-avg-flux"
