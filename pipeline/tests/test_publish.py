"""publish.py: serialize/write only — the JSON contract, idempotence, truncation."""

from __future__ import annotations

import json

import numpy as np
import pytest

from scoreboard import publish
from scoreboard.config import Station

STATIONS = [
    Station(id="a", name="A", kind="wave", lat=1.0, lon=2.0,
            source="candhis", source_id="0001", baseline="marine-best"),
    Station(id="b", name="B", kind="tide", lat=3.0, lon=4.0,
            source="shom", source_id="0002", baseline="harmonic"),
]
GATE = {
    "a": {"pass": True, "weak": False, "mae_model": 0.1, "mae_baseline": 0.2,
          "gain": 0.5, "gain_debiased": 0.4, "baseline_model": "ewam"},
    # Pas de `baseline_model` : station de marée, la référence est l'harmonique.
    "b": {"pass": False, "weak": True, "mae_model": 0.3, "mae_baseline": 0.29,
          "gain": -0.03, "gain_debiased": -0.1},
}


def _day(date_str, mae_ia=0.1, mae_baseline=0.2):
    return {
        "date": date_str,
        "status": "ok",
        "series": [{"t": f"{date_str}T07:00:00Z", "obs": 1.4, "ia": 1.5, "baseline": 1.6}],
        "mae_ia": mae_ia,
        "mae_baseline": mae_baseline,
    }


# --- (a) score_day on known values --------------------------------------


def test_score_day_computes_mae_on_known_values():
    obs = [1.0, 2.0, 3.0]
    ia = [1.1, 1.8, 3.3]
    baseline = [1.5, 2.5, 2.5]
    mae_ia, mae_baseline = publish.score_day(obs, ia, baseline)
    assert mae_ia == pytest.approx(np.mean([0.1, 0.2, 0.3]))
    assert mae_baseline == pytest.approx(np.mean([0.5, 0.5, 0.5]))


# --- (b) history truncated to 90 days -----------------------------------


def test_history_is_truncated_to_90_days(tmp_path):
    for i in range(95):
        date_str = f"2026-{(i // 28) + 1:02d}-{(i % 28) + 1:02d}"
        publish.upsert_history(tmp_path, "a", _day(date_str))
    payload = json.loads((tmp_path / "a" / "history.json").read_text())
    assert len(payload["days"]) == 90
    # the oldest 5 must have been dropped, most recent kept
    dates = [d["date"] for d in payload["days"]]
    assert dates == sorted(dates)


# --- (c) idempotence: publishing twice is a no-op on disk ----------------


def test_publishing_the_same_day_twice_is_idempotent(tmp_path):
    publish.upsert_history(tmp_path, "a", _day("2026-07-29"))
    path = tmp_path / "a" / "history.json"
    first_bytes = path.read_bytes()

    publish.upsert_history(tmp_path, "a", _day("2026-07-29"))
    second_bytes = path.read_bytes()

    assert first_bytes == second_bytes
    payload = json.loads(second_bytes)
    assert len(payload["days"]) == 1  # no duplicate day


def test_write_stations_is_idempotent(tmp_path):
    publish.write_stations(tmp_path, STATIONS, GATE)
    first = (tmp_path / "stations.json").read_bytes()
    publish.write_stations(tmp_path, STATIONS, GATE)
    second = (tmp_path / "stations.json").read_bytes()
    assert first == second


def test_stations_carry_the_baseline_model_the_gate_chose(tmp_path):
    """Le site liste les stations en lisant `stations.json` seul : sans ce champ
    ici, nommer la référence dans le tableau coûterait une requête par station.
    Absent sur une station de marée, dont la référence est l'harmonique."""
    payload = publish.write_stations(tmp_path, STATIONS, GATE)

    by_id = {s["id"]: s for s in payload["stations"]}
    assert by_id["a"]["baseline_model"] == "ewam"
    assert "baseline_model" not in by_id["b"]


def test_atomic_write_leaves_no_tmp_file(tmp_path):
    publish.write_stations(tmp_path, STATIONS, GATE)
    leftovers = list(tmp_path.glob("*.tmp"))
    assert leftovers == []


# --- (d) failed station -> "missing" day, scores unaffected --------------


def test_missing_day_does_not_move_the_aggregated_scores(tmp_path):
    for d in ["2026-07-27", "2026-07-28", "2026-07-29"]:
        publish.upsert_history(tmp_path, "a", _day(d, mae_ia=0.1, mae_baseline=0.2))
    before = publish.compute_scores(
        json.loads((tmp_path / "a" / "history.json").read_text())["days"]
    )

    publish.upsert_history(tmp_path, "a", {"date": "2026-07-30", "status": "missing"})

    payload = json.loads((tmp_path / "a" / "history.json").read_text())
    missing_entry = next(d for d in payload["days"] if d["date"] == "2026-07-30")
    assert missing_entry == {"date": "2026-07-30", "status": "missing"}

    after = publish.compute_scores(payload["days"])
    assert after == before
    assert after["n_days"] == 3  # the missing day does not count


# --- (e) windows are calendar-based, not "last N ok days" ---------------


def test_score_windows_are_calendar_based_not_count_based():
    # anchor = the latest ok date = 2026-07-30 (anchor-1 below). ok days also
    # at anchor-5 and anchor-20 — only anchor-1 and anchor-5 fall within 7
    # calendar days of the anchor, even though all three are the 3 most
    # recent "ok" entries (the old `ok[-3:]` behavior would include all of them).
    days = [
        _day("2026-07-11", mae_ia=0.9, mae_baseline=0.9),  # anchor-20 (outside 7d)
        _day("2026-07-26", mae_ia=0.2, mae_baseline=0.4),  # anchor-5 (inside 7d)
        _day("2026-07-30", mae_ia=0.4, mae_baseline=0.6),  # anchor-1 == anchor (inside 7d)
    ]
    row = publish.compute_scores(days)
    # 7d window: only the anchor-5 and anchor days (2 entries), not anchor-20.
    assert row["mae_ia_7d"] == pytest.approx((0.2 + 0.4) / 2)
    assert row["mae_baseline_7d"] == pytest.approx((0.4 + 0.6) / 2)
    # 30d window includes all three.
    assert row["mae_ia_30d"] == pytest.approx((0.9 + 0.2 + 0.4) / 3)
    assert row["n_days"] == 3


# --- schema contract -----------------------------------------------------


def test_stations_json_has_schema_version_and_gate_flags(tmp_path):
    payload = publish.write_stations(tmp_path, STATIONS, GATE)
    assert payload["schema_version"] == 1
    by_id = {s["id"]: s for s in payload["stations"]}
    assert by_id["a"]["published"] is True
    assert by_id["a"]["weak"] is False
    assert by_id["b"]["published"] is False  # FAIL station stays listed
    assert by_id["b"]["weak"] is True
    assert by_id["a"]["unit"] == "m"


def test_latest_json_has_schema_version(tmp_path):
    payload = publish.write_latest(
        tmp_path, "a", "2026-07-30T06:00:00Z",
        [{"t": "2026-07-30T07:00:00Z", "ia": 1.42, "baseline": 1.55}],
    )
    assert payload["schema_version"] == 1
    on_disk = json.loads((tmp_path / "a" / "latest.json").read_text())
    assert on_disk == payload


def test_history_json_has_schema_version(tmp_path):
    payload = publish.upsert_history(tmp_path, "a", _day("2026-07-29"))
    assert payload["schema_version"] == 1


def test_scores_json_has_schema_version_and_aggregates(tmp_path):
    for d in ["2026-07-27", "2026-07-28", "2026-07-29"]:
        publish.upsert_history(tmp_path, "a", _day(d, mae_ia=0.1, mae_baseline=0.2))
    payload = publish.write_scores(tmp_path, ["a"], "2026-07-30T07:00:00Z")
    assert payload["schema_version"] == 1
    row = payload["stations"][0]
    assert row["id"] == "a"
    assert row["n_days"] == 3
    assert row["mae_ia_7d"] == pytest.approx(0.1)
    assert row["mae_baseline_all"] == pytest.approx(0.2)


# --- Task 6 fix 2: score windows are per baseline_model ---------------------


def _ok_day(date_str, mae_ia, mae_baseline, baseline_model=None):
    entry = {
        "date": date_str, "status": "ok", "series": [],
        "mae_ia": mae_ia, "mae_baseline": mae_baseline,
        "n_points": 1, "max_lead_h": 1,
    }
    if baseline_model:
        entry["baseline_model"] = baseline_model
    return entry


def test_windows_ignore_days_scored_against_a_previous_baseline():
    """Merge-day scenario: 3 legacy MFWAM days then 2 on the new best model.
    Averaging them would publish a hybrid gain nobody labelled."""
    days = [
        _ok_day("2026-07-28", 0.90, 1.00),  # legacy: no baseline_model key
        _ok_day("2026-07-29", 0.90, 1.00),
        _ok_day("2026-07-30", 0.90, 1.00),
        _ok_day("2026-07-31", 0.10, 0.20, "ewam"),
        _ok_day("2026-08-01", 0.30, 0.40, "ewam"),
    ]

    row = publish.compute_scores(days)

    assert row["n_days"] == 2  # only the comparable days
    assert row["mae_ia_7d"] == 0.2  # (0.10 + 0.30) / 2, legacy days excluded
    assert row["mae_baseline_7d"] == 0.3
    assert row["mae_ia_all"] == 0.2  # "all" is a window too, not a loophole


def test_a_third_baseline_supersedes_the_second():
    """Only the *current* baseline counts — an intermediate one is dropped too."""
    days = [
        _ok_day("2026-07-30", 0.90, 1.00, "mfwam"),
        _ok_day("2026-07-31", 0.10, 0.20, "ewam"),
        _ok_day("2026-08-01", 0.50, 0.60, "ncep_gfswave025"),
    ]

    row = publish.compute_scores(days)

    assert row["n_days"] == 1
    assert row["mae_ia_all"] == 0.5


def test_only_legacy_days_left_yields_empty_windows_not_a_crash():
    """The day after the switch: the newest day names a baseline no older day
    has, so every window is empty. Nulls, no ZeroDivisionError."""
    days = [
        _ok_day("2026-07-30", 0.90, 1.00),
        _ok_day("2026-07-31", 0.90, 1.00),
        {"date": "2026-08-01", "status": "missing", "baseline_model": "ewam"},
    ]

    row = publish.compute_scores(days)

    assert row["n_days"] == 0
    for label in ("7d", "30d", "all"):
        assert row[f"mae_ia_{label}"] is None
        assert row[f"mae_baseline_{label}"] is None


def test_tide_history_without_baseline_model_is_unaffected():
    """No day names a baseline (harmonic) → the pre-Task-6 behaviour, verbatim."""
    days = [
        _ok_day("2026-07-30", 0.10, 0.20),
        _ok_day("2026-07-31", 0.30, 0.40),
    ]

    row = publish.compute_scores(days)

    assert row["n_days"] == 2
    assert row["mae_ia_all"] == 0.2
    assert row["mae_baseline_all"] == 0.3


def test_backfilled_count_follows_the_same_window():
    days = [
        _ok_day("2026-07-30", 0.90, 1.00) | {"backfilled": True},
        _ok_day("2026-07-31", 0.10, 0.20, "ewam") | {"backfilled": True},
        _ok_day("2026-08-01", 0.30, 0.40, "ewam"),
    ]

    row = publish.compute_scores(days)

    assert row["n_days"] == 2
    assert row["n_days_backfilled"] == 1  # the legacy backfilled day is out
