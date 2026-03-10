"""Integration tests for validation flow: fetch -> validation -> browse_parameters."""
import json
from unittest.mock import patch

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
                {"name": "BY", "type": "double", "units": "nT", "description": "B theta"},
            ],
            "startDate": "2024-01-01",
            "stopDate": "2024-12-31",
        }))

        # Create validation records with a missing field
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

        with patch(
            "pdsmcp.metadata._dataset_id_to_cache_filename",
            return_value="test_DATASET.json",
        ):
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

        with patch(
            "pdsmcp.metadata._dataset_id_to_cache_filename",
            return_value="test_NOVAL.json",
        ):
            from pdsmcp.metadata import browse_parameters
            result = browse_parameters(dataset_id="test:NOVAL")

        assert result["status"] == "success"
        assert result["validation"] is None


class TestCacheIncludesValidation:
    """Test that cache management includes the validation category."""

    def test_cache_status_has_validation(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PDSMCP_CACHE_DIR", str(tmp_path))
        from pdsmcp.cache import cache_status
        result = cache_status()
        assert "validation" in result["categories"]

    def test_cache_clean_all_includes_validation(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PDSMCP_CACHE_DIR", str(tmp_path))

        # Create a validation file
        val_dir = tmp_path / "validation"
        val_dir.mkdir()
        (val_dir / "test.json").write_text("{}")

        from pdsmcp.cache import cache_clean
        result = cache_clean(category="all", dry_run=True)
        assert result["deleted_count"] >= 1
