"""`data_coverage.missing_days` — détection des trous de calendrier."""

from __future__ import annotations

import importlib.util
import sys
from datetime import date
from pathlib import Path

_SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "data_coverage.py"


def _load_data_coverage():
    spec = importlib.util.spec_from_file_location("data_coverage", _SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["data_coverage"] = module
    spec.loader.exec_module(module)
    return module


data_coverage = _load_data_coverage()
missing_days = data_coverage.missing_days


def test_no_gap_when_all_days_present():
    first, last = date(2026, 8, 1), date(2026, 8, 3)
    present = {date(2026, 8, 1), date(2026, 8, 2), date(2026, 8, 3)}
    assert missing_days(present, first, last) == []


def test_reports_missing_days_inside_the_interval():
    first, last = date(2026, 8, 1), date(2026, 8, 5)
    present = {date(2026, 8, 1), date(2026, 8, 3), date(2026, 8, 5)}
    assert missing_days(present, first, last) == [date(2026, 8, 2), date(2026, 8, 4)]


def test_empty_interval_when_first_after_last():
    assert missing_days(set(), date(2026, 8, 5), date(2026, 8, 1)) == []
