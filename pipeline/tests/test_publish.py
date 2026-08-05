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
    """Same contract daily.run() relies on: passing the same `updated` twice (the
    same `run_date`'s deterministic `issued`) must write byte-identical output,
    never `datetime.now()`."""
    publish.write_stations(tmp_path, STATIONS, GATE, updated="2026-07-30T06:00:00Z")
    first = (tmp_path / "stations.json").read_bytes()
    publish.write_stations(tmp_path, STATIONS, GATE, updated="2026-07-30T06:00:00Z")
    second = (tmp_path / "stations.json").read_bytes()
    assert first == second


def test_stations_json_carries_the_updated_timestamp(tmp_path):
    payload = publish.write_stations(tmp_path, STATIONS, GATE, updated="2026-07-30T06:00:00Z")
    assert payload["updated"] == "2026-07-30T06:00:00Z"
    on_disk = json.loads((tmp_path / "stations.json").read_text())
    assert on_disk["updated"] == "2026-07-30T06:00:00Z"


def test_stations_json_omits_updated_on_a_true_cold_start(tmp_path):
    """Backfill calls `write_stations` without `updated`: on an empty data/ dir
    the key is simply absent — never stamped with wall-clock time, so a no-op
    backfill keeps `stations.json` byte-identical."""
    payload = publish.write_stations(tmp_path, STATIONS, GATE)
    assert "updated" not in payload


def test_stations_json_preserves_updated_written_by_a_previous_run(tmp_path):
    """A warm backfill (no `updated` of its own) must not clobber the freshness
    timestamp the last daily run published — it reads it back off disk."""
    publish.write_stations(tmp_path, STATIONS, GATE, updated="2026-07-30T06:00:00Z")
    payload = publish.write_stations(tmp_path, STATIONS, GATE)
    assert payload["updated"] == "2026-07-30T06:00:00Z"


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


def test_scores_weight_each_valid_observation_and_keep_baseline_windows_separate():
    """Une MAE issue de trois heures pèse trois fois celle issue d'une heure.

    Les valeurs attendues viennent d'un calcul manuel, pas de l'algorithme :
    ``(1 * 1 + 3 * 3) / (1 + 3) = 2.5``. Le jour MFWAM, pourtant beaucoup
    plus dense, ne doit pas contaminer les fenêtres de la baseline EWAM.
    """
    days = [
        _ok_day("2026-07-29", 0.0, 0.0, "mfwam") | {"n_points": 100},
        _ok_day("2026-07-26", 1.0, 4.0, "ewam") | {"n_points": 1},
        _ok_day("2026-07-30", 3.0, 6.0, "ewam") | {"n_points": 3},
    ]

    row = publish.compute_scores(days)

    assert row["n_days"] == 2
    for label in ("7d", "30d", "all"):
        assert row[f"mae_ia_{label}"] == 2.5
        assert row[f"mae_baseline_{label}"] == 5.5


@pytest.mark.parametrize("n_points", [None, 0, -2, "oops", True, 1.5, float("nan")])
def test_legacy_or_invalid_n_points_falls_back_to_one_day_weight(n_points):
    """Les historiques antérieurs et les compteurs corrompus restent publiables."""
    invalid = _ok_day("2026-07-30", 3.0, 6.0) | {"n_points": n_points}
    legacy = _ok_day("2026-07-29", 1.0, 4.0)
    if n_points is None:
        invalid.pop("n_points")  # forme legacy : champ absent

    row = publish.compute_scores([legacy, invalid])

    assert row["mae_ia_all"] == 2.0
    assert row["mae_baseline_all"] == 5.0


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


def test_scores_json_status_comes_from_the_run_summary(tmp_path):
    """`status` is *today's* issuance verdict, not derived from history — a
    station can be `"missing"` today even if yesterday's history entry says
    `"ok"` (or the reverse), so it must come straight from the caller."""
    for d in ["2026-07-27", "2026-07-28"]:
        publish.upsert_history(tmp_path, "a", _day(d))
    publish.upsert_history(tmp_path, "b", _day("2026-07-27"))
    payload = publish.write_scores(
        tmp_path, ["a", "b"], "2026-07-30T07:00:00Z", {"a": "ok", "b": "missing"}
    )
    by_id = {row["id"]: row["status"] for row in payload["stations"]}
    assert by_id == {"a": "ok", "b": "missing"}


def test_scores_json_status_falls_back_to_history_when_no_summary_given(tmp_path):
    """Backward-compat call (backfill): no `statuses` dict passed, so the field
    still exists, sourced from each station's most recent history day."""
    publish.upsert_history(tmp_path, "a", _day("2026-07-29"))
    publish.upsert_history(tmp_path, "b", {"date": "2026-07-29", "status": "missing"})
    payload = publish.write_scores(tmp_path, ["a", "b", "c"], "2026-07-30T07:00:00Z")
    by_id = {row["id"]: row["status"] for row in payload["stations"]}
    assert by_id == {"a": "ok", "b": "missing", "c": "missing"}  # "c": no history at all


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


# --- by_lead: point-by-point MAE decomposition by lead time ------------


def _series_point(hour_offset, obs=1.0, ia=1.1, baseline=1.2):
    """A point at lead `hour_offset` after a 2026-07-30 06:00 UTC emission."""
    from datetime import datetime, timedelta, timezone

    t = datetime(2026, 7, 30, 6, tzinfo=timezone.utc) + timedelta(hours=hour_offset)
    return {"t": t.isoformat().replace("+00:00", "Z"), "obs": obs, "ia": ia, "baseline": baseline}


def test_by_lead_buckets_points_by_known_leads():
    day = {
        "date": "2026-07-30",
        "status": "ok",
        "mae_ia": 0.1,
        "mae_baseline": 0.2,
        "n_points": 4,
        "series": [
            _series_point(6, obs=1.0, ia=1.1, baseline=1.3),   # h06: |err|=.1/.3
            _series_point(12, obs=1.0, ia=1.2, baseline=1.5),  # h12: |err|=.2/.5
            _series_point(24, obs=1.0, ia=1.4, baseline=1.9),  # h24: |err|=.4/.9
            _series_point(48, obs=1.0, ia=1.8, baseline=2.9),  # h48: |err|=.8/1.9
        ],
    }
    by_lead = publish.compute_lead_breakdown([day])
    assert by_lead["h06"] == {"mae_ia": 0.1, "mae_baseline": 0.3, "n_points": 1}
    assert by_lead["h12"] == {"mae_ia": 0.2, "mae_baseline": 0.5, "n_points": 1}
    assert by_lead["h24"] == {"mae_ia": pytest.approx(0.4), "mae_baseline": pytest.approx(0.9), "n_points": 1}
    assert by_lead["h48"] == {"mae_ia": pytest.approx(0.8), "mae_baseline": pytest.approx(1.9), "n_points": 1}


def test_by_lead_excludes_a_day_scored_against_an_old_baseline():
    days = [
        _ok_day("2026-07-29", 0.9, 1.0, "mfwam") | {
            "series": [_series_point(6, obs=1.0, ia=1.5, baseline=1.9)]
        },
        _ok_day("2026-07-30", 0.1, 0.2, "ewam") | {
            "series": [_series_point(6, obs=1.0, ia=1.1, baseline=1.2)]
        },
    ]
    # Via `compute_scores`, le chemin de prod : le filtre baseline vit chez
    # l'appelant depuis que `compute_lead_breakdown` reçoit la fenêtre déjà
    # filtrée.
    by_lead = publish.compute_scores(days)["by_lead"]
    assert by_lead["h06"]["n_points"] == 1
    assert by_lead["h06"]["mae_ia"] == pytest.approx(0.1)


def test_by_lead_ignores_legacy_days_without_series():
    days = [_ok_day("2026-07-30", 0.1, 0.2)]  # `_ok_day` has an empty `series`
    by_lead = publish.compute_lead_breakdown(days)
    for label in publish.LEAD_BUCKETS:
        assert by_lead[label] == {"mae_ia": None, "mae_baseline": None, "n_points": 0}


def test_by_lead_respects_the_30d_window():
    recent = {
        "date": "2026-07-30",
        "status": "ok",
        "mae_ia": 0.1,
        "mae_baseline": 0.2,
        "n_points": 1,
        "series": [_series_point(6, obs=1.0, ia=1.1, baseline=1.2)],
    }
    too_old = {
        "date": "2026-06-20",  # 40 days before the anchor
        "status": "ok",
        "mae_ia": 0.9,
        "mae_baseline": 1.0,
        "n_points": 1,
        "series": [
            {"t": "2026-06-20T12:00:00Z", "obs": 1.0, "ia": 5.0, "baseline": 6.0}
        ],
    }
    # Via `compute_scores` : le fenêtrage 30d vit chez l'appelant, voir ci-dessus.
    by_lead = publish.compute_scores([too_old, recent])["by_lead"]
    assert by_lead["h06"]["n_points"] == 1
    assert by_lead["h06"]["mae_ia"] == pytest.approx(0.1)


def test_compute_scores_carries_by_lead(tmp_path):
    day = {
        "date": "2026-07-30",
        "status": "ok",
        "mae_ia": 0.1,
        "mae_baseline": 0.2,
        "n_points": 1,
        "series": [_series_point(6, obs=1.0, ia=1.1, baseline=1.2)],
    }
    row = publish.compute_scores([day])
    assert row["by_lead"]["h06"]["n_points"] == 1
    assert row["by_lead"]["h12"] == {"mae_ia": None, "mae_baseline": None, "n_points": 0}


# --- metrics_30d: RMSE / biais / R² point à point ---------------------------


def test_window_metrics_match_a_manual_calculation():
    """obs=[1,2,3], ia=[1.5,2.5,2.5], baseline=[2,2,4].

    err_ia = [.5,.5,-.5]      -> rmse_ia = sqrt(mean(.25,.25,.25)) = 0.5
                               -> bias_ia = mean(.5,.5,-.5) = 1/6 ≈ 0.1667
    err_baseline = [1,0,1]    -> rmse_baseline = sqrt(mean(1,0,1)) = sqrt(2/3) ≈ 0.8165
                               -> bias_baseline = mean(1,0,1) = 2/3 ≈ 0.6667
    obs mean = 2, ss_tot = (1-2)²+(2-2)²+(3-2)² = 2
    ss_res_ia = .25+.25+.25 = .75       -> r2_ia = 1 - .75/2 = 0.625
    ss_res_baseline = 1+0+1 = 2         -> r2_baseline = 1 - 2/2 = 0.0
    """
    day = {
        "date": "2026-07-30",
        "series": [
            {"t": "2026-07-30T07:00:00Z", "obs": 1, "ia": 1.5, "baseline": 2},
            {"t": "2026-07-30T13:00:00Z", "obs": 2, "ia": 2.5, "baseline": 2},
            {"t": "2026-07-31T07:00:00Z", "obs": 3, "ia": 2.5, "baseline": 4},
        ],
    }
    metrics = publish.compute_window_metrics([day])
    assert metrics == {
        "rmse_ia": 0.5,
        "rmse_baseline": pytest.approx(0.8165),
        "bias_ia": pytest.approx(0.1667),
        "bias_baseline": pytest.approx(0.6667),
        "r2_ia": 0.625,
        "r2_baseline": 0.0,
        "n_points": 3,
    }


def test_window_metrics_r2_is_none_when_obs_variance_is_zero():
    day = {
        "date": "2026-07-30",
        "series": [
            {"t": "2026-07-30T07:00:00Z", "obs": 2, "ia": 2.1, "baseline": 1.9},
            {"t": "2026-07-30T13:00:00Z", "obs": 2, "ia": 1.9, "baseline": 2.1},
        ],
    }
    metrics = publish.compute_window_metrics([day])
    assert metrics["r2_ia"] is None
    assert metrics["r2_baseline"] is None
    assert metrics["n_points"] == 2
    # RMSE/bias restent calculables même sans variance des obs.
    assert metrics["rmse_ia"] is not None


def test_window_metrics_r2_is_none_with_a_single_point():
    day = {"date": "2026-07-30", "series": [{"t": "2026-07-30T07:00:00Z", "obs": 1, "ia": 1.1, "baseline": 0.9}]}
    metrics = publish.compute_window_metrics([day])
    assert metrics["n_points"] == 1
    assert metrics["r2_ia"] is None
    assert metrics["r2_baseline"] is None


def test_window_metrics_ignore_legacy_days_without_series():
    days = [_ok_day("2026-07-30", 0.1, 0.2)]  # `_ok_day` has an empty `series`
    metrics = publish.compute_window_metrics(days)
    assert metrics == {
        "rmse_ia": None,
        "rmse_baseline": None,
        "bias_ia": None,
        "bias_baseline": None,
        "r2_ia": None,
        "r2_baseline": None,
        "n_points": 0,
    }


def test_window_metrics_ignore_points_without_obs():
    day = {
        "date": "2026-07-30",
        "series": [
            {"t": "2026-07-30T07:00:00Z", "obs": None, "ia": 1.5, "baseline": 1.6},
            {"t": "2026-07-30T13:00:00Z", "obs": 1.0, "ia": 1.1, "baseline": 1.2},
        ],
    }
    metrics = publish.compute_window_metrics([day])
    assert metrics["n_points"] == 1


def test_compute_scores_carries_metrics_30d():
    day = {
        "date": "2026-07-30",
        "status": "ok",
        "mae_ia": 0.1,
        "mae_baseline": 0.2,
        "n_points": 1,
        "series": [_series_point(6, obs=1.0, ia=1.1, baseline=1.2)],
    }
    row = publish.compute_scores([day])
    assert row["metrics_30d"] == publish.compute_window_metrics([day])
    assert row["metrics_30d"]["n_points"] == 1


def test_station_entry_publishes_the_unit_of_its_kind(tmp_path):
    """`unit` est une donnée publique : une station de vent servie en mètres est
    une erreur de fait, pas un détail d'affichage."""
    stations = [
        Station(id="w", name="W", kind="wave", lat=1.0, lon=2.0,
                source="candhis", source_id="1", baseline="marine-best"),
        Station(id="t", name="T", kind="tide", lat=3.0, lon=4.0,
                source="shom", source_id="2", baseline="harmonic"),
        Station(id="v", name="V", kind="wind", lat=5.0, lon=6.0,
                source="mfobs", source_id="3", baseline="wind-best"),
    ]
    payload = publish.write_stations(tmp_path, stations, gate={})
    units = {s["id"]: s["unit"] for s in payload["stations"]}
    assert units == {"w": "m", "t": "m", "v": "m/s"}
