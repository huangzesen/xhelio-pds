"""Tests for PDS schema consistency validation."""
import json
from pathlib import Path

import pytest

from pdsmcp.validation import (
    _extract_data_fields,
    _validation_filename,
    flush_validations,
    get_validation_summary,
)


def _make_label(fields):
    """Helper to build a minimal parsed label dict."""
    return {"fields": fields, "table_type": "fixed"}


class TestExtractDataFields:
    def test_extracts_non_time_fields(self):
        label = _make_label([
            {"name": "TIME", "type": "ASCII_Date_Time", "unit": "", "offset": 1, "length": 24},
            {"name": "BX", "type": "ASCII_Real", "unit": "nT", "offset": 26, "length": 15},
            {"name": "BY", "type": "ASCII_Real", "unit": "nT", "offset": 42, "length": 15},
        ])
        result = _extract_data_fields(label)
        assert "BX" in result
        assert "BY" in result
        assert "TIME" not in result
        assert result["BX"]["units"] == "nT"
        assert result["BX"]["type"] == "ASCII_Real"

    def test_skips_epoch_utc_date(self):
        label = _make_label([
            {"name": "EPOCH", "type": "double", "unit": "", "offset": 1, "length": 20},
            {"name": "UTC", "type": "CHAR", "unit": "", "offset": 21, "length": 24},
            {"name": "SCET", "type": "double", "unit": "", "offset": 45, "length": 15},
            {"name": "VALUE", "type": "double", "unit": "V", "offset": 61, "length": 10},
        ])
        result = _extract_data_fields(label)
        assert list(result.keys()) == ["VALUE"]

    def test_empty_fields(self):
        label = _make_label([])
        result = _extract_data_fields(label)
        assert result == {}

    def test_includes_offset_length_size(self):
        label = _make_label([
            {"name": "BR", "type": "double", "unit": "nT", "offset": 1, "length": 15,
             "description": "radial B"},
        ])
        result = _extract_data_fields(label)
        assert result["BR"]["offset"] == 1
        assert result["BR"]["length"] == 15


class TestFlushValidations:
    def test_first_label_sets_reference_schema(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PDSMCP_CACHE_DIR", str(tmp_path))
        label = _make_label([
            {"name": "TIME", "type": "ASCII_Date_Time", "unit": "", "offset": 1, "length": 24},
            {"name": "BX", "type": "double", "unit": "nT", "offset": 26, "length": 15},
        ])
        flush_validations("test:DATASET", [
            (label, "FILE_001.LBL", "https://archive/FILE_001.LBL"),
        ])

        vfile = tmp_path / "validation" / _validation_filename("test:DATASET")
        assert vfile.exists()
        data = json.loads(vfile.read_text())
        assert data["reference_schema"]["source_file"] == "FILE_001.LBL"
        assert "BX" in data["reference_schema"]["fields"]
        assert "TIME" not in data["reference_schema"]["fields"]
        assert len(data["_validations"]) == 1

    def test_consistent_labels_no_issues(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PDSMCP_CACHE_DIR", str(tmp_path))
        label1 = _make_label([
            {"name": "TIME", "type": "t", "unit": "", "offset": 1, "length": 24},
            {"name": "BX", "type": "double", "unit": "nT", "offset": 26, "length": 15},
            {"name": "BY", "type": "double", "unit": "nT", "offset": 42, "length": 15},
        ])
        label2 = _make_label([
            {"name": "TIME", "type": "t", "unit": "", "offset": 1, "length": 24},
            {"name": "BX", "type": "double", "unit": "nT", "offset": 26, "length": 15},
            {"name": "BY", "type": "double", "unit": "nT", "offset": 42, "length": 15},
        ])
        flush_validations("test:DS", [
            (label1, "F1.LBL", "https://archive/F1.LBL"),
            (label2, "F2.LBL", "https://archive/F2.LBL"),
        ])

        vfile = tmp_path / "validation" / _validation_filename("test:DS")
        data = json.loads(vfile.read_text())
        assert data["schema_annotations"]["BX"]["presence_ratio"] == 1.0
        assert data["schema_annotations"]["BX"]["drift"] == []
        assert len(data["_validations"]) == 2

    def test_missing_field_detected(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PDSMCP_CACHE_DIR", str(tmp_path))
        label_full = _make_label([
            {"name": "BX", "type": "double", "unit": "nT", "offset": 1, "length": 15},
            {"name": "BY", "type": "double", "unit": "nT", "offset": 16, "length": 15},
        ])
        label_partial = _make_label([
            {"name": "BX", "type": "double", "unit": "nT", "offset": 1, "length": 15},
        ])
        flush_validations("test:DS2", [
            (label_full, "F1.LBL", "https://archive/F1.LBL"),
            (label_partial, "F2.LBL", "https://archive/F2.LBL"),
        ])

        vfile = tmp_path / "validation" / _validation_filename("test:DS2")
        data = json.loads(vfile.read_text())
        assert data["schema_annotations"]["BY"]["files_present"] == 1
        assert data["schema_annotations"]["BY"]["presence_ratio"] == 0.5
        assert data["_validations"][1]["missing_fields"] == ["BY"]

    def test_new_field_detected(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PDSMCP_CACHE_DIR", str(tmp_path))
        label1 = _make_label([
            {"name": "BX", "type": "double", "unit": "nT", "offset": 1, "length": 15},
        ])
        label2 = _make_label([
            {"name": "BX", "type": "double", "unit": "nT", "offset": 1, "length": 15},
            {"name": "BZ", "type": "double", "unit": "nT", "offset": 16, "length": 15},
        ])
        flush_validations("test:DS3", [
            (label1, "F1.LBL", "https://archive/F1.LBL"),
            (label2, "F2.LBL", "https://archive/F2.LBL"),
        ])

        vfile = tmp_path / "validation" / _validation_filename("test:DS3")
        data = json.loads(vfile.read_text())
        assert "BZ" in data["schema_annotations"]
        assert data["schema_annotations"]["BZ"]["files_present"] == 1
        assert data["_validations"][1]["new_fields"] == ["BZ"]

    def test_drift_detected(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PDSMCP_CACHE_DIR", str(tmp_path))
        label1 = _make_label([
            {"name": "BX", "type": "double", "unit": "nT", "offset": 1, "length": 15},
        ])
        label2 = _make_label([
            {"name": "BX", "type": "double", "unit": "nanotesla", "offset": 1, "length": 15},
        ])
        flush_validations("test:DS4", [
            (label1, "F1.LBL", "https://archive/F1.LBL"),
            (label2, "F2.LBL", "https://archive/F2.LBL"),
        ])

        vfile = tmp_path / "validation" / _validation_filename("test:DS4")
        data = json.loads(vfile.read_text())
        drift = data["schema_annotations"]["BX"]["drift"]
        assert len(drift) == 1
        assert drift[0]["field"] == "units"
        assert drift[0]["expected"] == "nT"
        assert drift[0]["actual"] == "nanotesla"
        assert drift[0]["first_seen_in"] == "F2.LBL"

    def test_dedup_by_source_url(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PDSMCP_CACHE_DIR", str(tmp_path))
        label = _make_label([
            {"name": "BX", "type": "double", "unit": "nT", "offset": 1, "length": 15},
        ])
        flush_validations("test:DS5", [
            (label, "F1.LBL", "https://archive/F1.LBL"),
        ])
        flush_validations("test:DS5", [
            (label, "F1.LBL", "https://archive/F1.LBL"),
        ])

        vfile = tmp_path / "validation" / _validation_filename("test:DS5")
        data = json.loads(vfile.read_text())
        assert len(data["_validations"]) == 1

    def test_incremental_across_calls(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PDSMCP_CACHE_DIR", str(tmp_path))
        label1 = _make_label([
            {"name": "BX", "type": "double", "unit": "nT", "offset": 1, "length": 15},
        ])
        label2 = _make_label([
            {"name": "BX", "type": "double", "unit": "nT", "offset": 1, "length": 15},
        ])
        flush_validations("test:DS6", [
            (label1, "F1.LBL", "https://archive/F1.LBL"),
        ])
        flush_validations("test:DS6", [
            (label2, "F2.LBL", "https://archive/F2.LBL"),
        ])

        vfile = tmp_path / "validation" / _validation_filename("test:DS6")
        data = json.loads(vfile.read_text())
        assert len(data["_validations"]) == 2
        assert data["schema_annotations"]["BX"]["files_seen"] == 2


class TestGetValidationSummary:
    def test_returns_none_when_no_validation(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PDSMCP_CACHE_DIR", str(tmp_path))
        result = get_validation_summary("nonexistent:DATASET")
        assert result is None

    def test_clean_dataset(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PDSMCP_CACHE_DIR", str(tmp_path))
        label = _make_label([
            {"name": "BX", "type": "double", "unit": "nT", "offset": 1, "length": 15},
            {"name": "BY", "type": "double", "unit": "nT", "offset": 16, "length": 15},
        ])
        flush_validations("test:CLEAN", [
            (label, "F1.LBL", "https://archive/F1.LBL"),
            (label, "F2.LBL", "https://archive/F2.LBL"),
        ])
        result = get_validation_summary("test:CLEAN")
        assert result["validated"] is True
        assert result["files_checked"] == 2
        assert result["issues"] == []
        assert "no issues" in result["summary"]

    def test_presence_issue(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PDSMCP_CACHE_DIR", str(tmp_path))
        label1 = _make_label([
            {"name": "BX", "type": "double", "unit": "nT", "offset": 1, "length": 15},
            {"name": "BY", "type": "double", "unit": "nT", "offset": 16, "length": 15},
        ])
        label2 = _make_label([
            {"name": "BX", "type": "double", "unit": "nT", "offset": 1, "length": 15},
        ])
        flush_validations("test:PARTIAL", [
            (label1, "F1.LBL", "https://a/F1.LBL"),
            (label2, "F2.LBL", "https://a/F2.LBL"),
        ])
        result = get_validation_summary("test:PARTIAL")
        assert len(result["issues"]) == 1
        assert result["issues"][0]["parameter"] == "BY"
        assert result["issues"][0]["presence_ratio"] == 0.5

    def test_drift_issue(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PDSMCP_CACHE_DIR", str(tmp_path))
        label1 = _make_label([
            {"name": "BX", "type": "double", "unit": "nT", "offset": 1, "length": 15},
        ])
        label2 = _make_label([
            {"name": "BX", "type": "double", "unit": "nanotesla", "offset": 1, "length": 15},
        ])
        flush_validations("test:DRIFT", [
            (label1, "F1.LBL", "https://a/F1.LBL"),
            (label2, "F2.LBL", "https://a/F2.LBL"),
        ])
        result = get_validation_summary("test:DRIFT")
        drift_issues = [i for i in result["issues"] if i.get("type") == "drift"]
        assert len(drift_issues) == 1
        assert drift_issues[0]["field"] == "units"
        assert drift_issues[0]["expected"] == "nT"
        assert drift_issues[0]["actual"] == "nanotesla"
