"""train.py: baseline selection (no test leak), per-lead router, artefact keys."""

from __future__ import annotations

import importlib.util
import json
import sys
from itertools import pairwise
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from scoreboard import model
from scoreboard.config import Station
from scoreboard.features import WAVE_FEATURE_COLUMNS
from scoreboard.sources.marine import MODEL_COLUMNS
from scoreboard.sources.wind import MULTI_FORCING_COLUMNS

_SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "train.py"


def _load_train():
    spec = importlib.util.spec_from_file_location("train", _SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["train"] = module
    spec.loader.exec_module(module)
    return module


train = _load_train()

STATION = Station(
    id="synthetic", name="Synthetic", kind="wave", lat=48.29, lon=-4.97,
    source="candhis", source_id="02911", baseline="marine-best",
)


def _raw(days: int = 45, seed: int = 0) -> pd.DataFrame:
    """Hourly raw frame: obs + 5 wave models + 6 wind columns, no NaN."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2026-01-01", periods=days * 24, freq="h", tz="UTC", name="time")
    hs = 2.0 + np.sin(np.arange(len(idx)) / 12.0) + rng.normal(0, 0.05, len(idx))
    raw = pd.DataFrame({"hs": hs}, index=idx)
    for col in MODEL_COLUMNS:
        raw[col] = hs + rng.normal(0.3, 0.2, len(idx))
    for col in MULTI_FORCING_COLUMNS:
        raw[col] = rng.normal(0.0, 5.0, len(idx))
    return raw


def test_select_baseline_ignores_the_test_days():
    """`hs_ewam` wins on the train days, `hs_gwam` on the test days.

    A selection that peeked at the test window would return `hs_gwam`.
    """
    raw = _raw(days=40)
    day = pd.DatetimeIndex(raw.index).normalize()
    all_days = day.unique()
    train_days = all_days[:30]
    is_train = day.isin(train_days)

    raw["hs_ewam"] = raw["hs"] + np.where(is_train, 0.01, 2.00)
    raw["hs_gwam"] = raw["hs"] + np.where(is_train, 0.10, 0.02)

    assert train.select_baseline(raw, train_days) == "hs_ewam"
    # …and the whole window really would have picked the other one.
    assert train.select_baseline(raw, all_days) == "hs_gwam"


def test_per_lead_router_routes_each_slice_to_its_own_model():
    """Relation flips at lead 24: predictions differ, and each row is served
    by the sub-model fitted on its own slice."""
    rng = np.random.default_rng(0)
    n = 900
    x = pd.DataFrame(
        {
            "baseline": rng.uniform(0.5, 5.0, n),
            "lead_h": rng.integers(1, 49, n),
            "last_err": rng.normal(0.0, 0.4, n),
            "mean_err_24h": rng.normal(0.0, 0.3, n),
            "hour_sin": rng.normal(0.0, 1.0, n),
            "hour_cos": rng.normal(0.0, 1.0, n),
            "wind_u10": rng.normal(0.0, 5.0, n),
            "wind_v10": rng.normal(0.0, 5.0, n),
        }
    )
    y = x["baseline"] + np.where(x["lead_h"] <= 24, 1.0, -1.0) * x["last_err"]

    m = model.train(x, y, name="hgb-per-lead")
    assert set(m.models_) == {(1, 12), (13, 24), (25, 48)}

    # Boundary leads included: 12 belongs to 1–12, 24 to 13–24, not to the next.
    leads = [1, 6, 12, 13, 18, 24, 25, 36, 48]
    owner = [(1, 12)] * 3 + [(13, 24)] * 3 + [(25, 48)] * 3
    probe = pd.concat([x.head(1)] * len(leads), ignore_index=True)
    probe["lead_h"] = leads
    pred = model.predict(m, probe)
    assert abs(pred[0] - pred[-1]) > 0.1  # the flip must show up across slices
    for i, slice_key in enumerate(owner):
        expected = m.models_[slice_key].predict(probe.iloc[[i]])[0]
        assert pred[i] == pytest.approx(expected)


def test_model_selection_never_looks_at_the_test_window(tmp_path, monkeypatch):
    """Candidate selection calls the scorer on validation, never on the test."""
    raw = _raw(days=120)
    raw["hs_gwam"] = raw["hs"] + 0.02
    monkeypatch.setattr(train, "DATA_DIR", tmp_path)
    monkeypatch.setattr(model, "MODELS_DIR", tmp_path)
    raw.to_parquet(tmp_path / "synthetic_raw.parquet")

    seen = []
    real_score = train._score
    monkeypatch.setattr(
        train, "_score", lambda level, x, obs: seen.append(len(x)) or real_score(level, x, obs)
    )
    row = train.evaluate(STATION, test_days=10, model_names=("ridge", "hgb"))

    # `_score` is used only for candidate selection on validation. Aggregate
    # test metrics follow a separate path after the winner is locked.
    assert len(seen) == 2
    assert seen[0] == seen[1] == row["n_val"] != row["n_test"]
    assert row["ml_model"] in ("ridge", "hgb")
    assert set(row["val_scores"]) == {"ridge", "hgb"}
    assert row["evaluation_protocol"] == "holdout dégradé"
    assert row["evaluation_ready"] is False
    assert row["pass"] is False


def test_wave_evaluation_uses_multiple_rolling_issue_day_folds_and_reports_ci(tmp_path, monkeypatch):
    """A wave verdict spans several origins; its uncertainty is resampled by
    issue day, never by correlated lead rows."""
    raw = _raw(days=160)
    raw["hs_gwam"] = raw["hs"] + 0.02
    monkeypatch.setattr(train, "DATA_DIR", tmp_path)
    raw.to_parquet(tmp_path / "synthetic_raw.parquet")

    monkeypatch.setattr(train, "SEASONAL_HISTORY_DAYS", 100)
    monkeypatch.setattr(train, "SEASONAL_STRIDE_DAYS", 10)
    row = train.evaluate(STATION, test_days=10, model_names=("ridge",))

    assert row["n_folds"] == 4
    assert row["n_test"] > 4 * 24 * 8
    assert row["test_days"] == 10
    assert row["gain_debiased_ci95_low"] <= row["gain_debiased"] <= row["gain_debiased_ci95_high"]
    assert row["ci_unit"] == "issue_day"
    assert row["evaluation_ready"] is True


def test_a_degenerate_origin_is_dropped_for_every_candidate_and_never_silently(
    tmp_path, monkeypatch
):
    """Two candidates must be scored on the same test rows, or a paired CI is void.

    The first origin here has a train period shorter than its own validation
    window, so no candidate can be ranked inside it. It must then be unusable for
    a forced candidate exactly as for the automatic selection — and it must say so.
    """
    raw = _raw(days=160)
    raw["hs_gwam"] = raw["hs"] + 0.02
    # Forcing gap over the first 112 days: `assemble` drops those issues, so the
    # first origin keeps far fewer train issue days than the observation history
    # suggests — the wave-station shape that exposed the asymmetry.
    raw.loc[raw.index < raw.index[0] + pd.Timedelta(days=112), MULTI_FORCING_COLUMNS] = np.nan
    monkeypatch.setattr(train, "DATA_DIR", tmp_path)
    monkeypatch.setattr(train, "SEASONAL_HISTORY_DAYS", 100)
    monkeypatch.setattr(train, "SEASONAL_STRIDE_DAYS", 10)
    raw.to_parquet(tmp_path / "synthetic_raw.parquet")

    forced = train.evaluate(STATION, test_days=10, model_names=("ridge",))
    auto = train.evaluate(STATION, test_days=10, model_names=("ridge", "hgb"))

    assert forced["n_folds"] == auto["n_folds"] < 4
    assert forced["_test_eval"][1].index.equals(auto["_test_eval"][1].index)
    assert any("validation" in reason for reason in forced["skipped_origins"])
    assert forced["skipped_origins"] == auto["skipped_origins"]


def test_write_report_publishes_skipped_origins(tmp_path, monkeypatch):
    """`skipped_origins` must reach the versioned report — not just stdout —
    without being propagated to `gate.json` (a station's `n_folds` alone can't
    tell a reader how many origins were planned but dropped)."""
    raw = _raw(days=160)
    raw["hs_gwam"] = raw["hs"] + 0.02
    raw.loc[raw.index < raw.index[0] + pd.Timedelta(days=112), MULTI_FORCING_COLUMNS] = np.nan
    monkeypatch.setattr(train, "DATA_DIR", tmp_path)
    monkeypatch.setattr(train, "SEASONAL_HISTORY_DAYS", 100)
    monkeypatch.setattr(train, "SEASONAL_STRIDE_DAYS", 10)
    monkeypatch.setattr(train, "REPORT_PATH", tmp_path / "model-eval.md")
    raw.to_parquet(tmp_path / "synthetic_raw.parquet")

    row = train.evaluate(STATION, test_days=10, model_names=("ridge",))
    assert row["skipped_origins"]  # the fixture must actually exercise the path

    gate = {row["station"]: {"pass": row["pass"], "weak": row["weak"]}}
    train.write_report([row], gate)
    report = train.REPORT_PATH.read_text()
    for reason in row["skipped_origins"]:
        assert reason in report

    gate_json = json.loads(json.dumps(train.merge_gate({}, [row], {row["station"]})))
    assert "skipped_origins" not in gate_json[row["station"]]


@pytest.mark.parametrize("holdout_days", [90, 120])
def test_rolling_origins_keep_whole_issue_days_and_purge_future_rows(holdout_days):
    idx = pd.date_range("2024-01-01", periods=800 * 24, freq="h", tz="UTC")
    x = pd.DataFrame({"lead_h": np.tile(np.arange(1, 25), 800)}, index=idx)
    splits = train.rolling_origin_splits(x, test_days=holdout_days, folds=4)

    assert len(splits) == 4
    days = train.issue_days(x)
    seen = set()
    starts = []
    for train_mask, test_mask in splits:
        train_days = set(days[train_mask])
        fold_days = set(days[test_mask])
        assert not seen.intersection(fold_days)
        assert not train_days.intersection(fold_days)
        seen.update(fold_days)
        starts.append(min(fold_days))
        assert max(train_days) <= min(fold_days) - pd.Timedelta(days=3)
        # Every lead from an issue is on the same side of the fold boundary.
        assert all(test_mask[days == day].all() for day in fold_days)
        assert all(train_mask[days == day].all() for day in train_days)
    stride = max(train.SEASONAL_STRIDE_DAYS, holdout_days)
    assert [(right - left).days for left, right in pairwise(starts)] == [stride] * 3
    assert (max(seen) - min(seen)).days >= stride * 3 + holdout_days - 1


def test_each_rolling_origin_selects_its_baseline_without_later_observations(
    tmp_path, monkeypatch
):
    raw = _raw(days=160)
    raw["hs_gwam"] = raw["hs"] + 0.02
    monkeypatch.setattr(train, "DATA_DIR", tmp_path)
    monkeypatch.setattr(train, "SEASONAL_HISTORY_DAYS", 100)
    monkeypatch.setattr(train, "SEASONAL_STRIDE_DAYS", 10)
    raw.to_parquet(tmp_path / "synthetic_raw.parquet")
    selections = []
    selected_columns = [*MODEL_COLUMNS[:4], MODEL_COLUMNS[-1]]

    def record_selection(frame, train_days, *args):
        selections.append(pd.DatetimeIndex(train_days).max())
        return selected_columns[len(selections) - 1]

    monkeypatch.setattr(train, "select_baseline", record_selection)

    row = train.evaluate(STATION, test_days=10, model_names=("ridge",))

    assert row["n_folds"] == 4
    assert selections[:4] == sorted(selections[:4])
    assert len(set(selections[:4])) == 4
    assert selections[-1] > selections[-2]  # separate production refit on all history
    assert row["fold_baselines"] == [column.removeprefix("hs_") for column in selected_columns[:4]]
    assert row["baseline_model"] == selected_columns[-1].removeprefix("hs_")


def test_gate_rejects_a_positive_point_gain_when_its_ci_crosses_zero(tmp_path, monkeypatch):
    raw = _raw(days=120)
    raw["hs_gwam"] = raw["hs"] + 0.02
    monkeypatch.setattr(train, "DATA_DIR", tmp_path)
    raw.to_parquet(tmp_path / "synthetic_raw.parquet")
    monkeypatch.setattr(
        train,
        "_debiased_baseline_error",
        lambda residual, fold_ids: np.ones_like(residual, dtype=float),
    )
    monkeypatch.setattr(train, "_gain_confidence_interval", lambda *_args: (-0.01, 0.2))

    row = train.evaluate(STATION, test_days=10, model_names=("ridge",))

    assert row["gain_debiased"] > train.GATE
    assert row["gain_debiased_ci95_low"] < 0
    assert not row["pass"]


def test_merge_gate_keeps_skipped_stations_and_drops_retired_ones():
    previous = {
        "brest": {"pass": False, "weak": True, "gain": -0.1},  # skipped this run
        "old-station": {"pass": True, "weak": False},  # no longer in stations.toml
        "anglet": {"pass": False, "weak": True, "gain": 0.0},  # retrained below
    }
    rows = [
        {
            "station": "anglet", "baseline_model": "ewam", "pass": True, "weak": False,
            "mae_model": 0.1063, "mae_base": 0.1178, "gain": 0.0978, "gain_debiased": 0.0543,
        }
    ]

    gate = train.merge_gate(previous, rows, known={"anglet", "brest"})

    assert gate["brest"] == previous["brest"]  # untouched, verdict preserved
    assert "old-station" not in gate  # evicted with the config entry
    assert gate["anglet"] == {
        "pass": True, "weak": False, "mae_model": 0.1063, "mae_baseline": 0.1178,
        "gain": 0.0978, "gain_debiased": 0.0543, "baseline_model": "ewam",
    }


def test_merge_gate_persists_the_station_uncertainty_contract():
    row = {
        "station": "anglet", "baseline_model": "ewam", "pass": True,
        "weak": False, "mae_model": 0.1, "mae_base": 0.2, "gain": 0.5,
        "gain_debiased": 0.4, "gain_debiased_ci95_low": 0.123456,
        "gain_debiased_ci95_high": 0.654321, "n_folds": 4,
        "n_issue_days": 360, "evaluation_protocol": "rolling-origin multi-saisons",
        "evaluation_ready": True, "ci_unit": "issue_day",
        "fold_baselines": ["ewam", "gwam", "ewam", "mfwam"],
    }

    gate = train.merge_gate({}, [row], known={"anglet"})

    assert gate["anglet"] == {
        "pass": True, "weak": False, "mae_model": 0.1, "mae_baseline": 0.2,
        "gain": 0.5, "gain_debiased": 0.4, "baseline_model": "ewam",
        "gain_debiased_ci95_low": 0.1235, "gain_debiased_ci95_high": 0.6543,
        "n_folds": 4, "n_issue_days": 360,
        "evaluation_protocol": "rolling-origin multi-saisons", "evaluation_ready": True,
        "ci_unit": "issue_day", "fold_baselines": ["ewam", "gwam", "ewam", "mfwam"],
    }


def test_movement_reads_the_delta_against_the_previous_gate():
    row = {"station": "brest", "gain_debiased": 0.52, "pass": True, "weak": False}

    improved = train._movement(row, {"brest": {"gain_debiased": 0.50}})
    fresh = train._movement(row, {})
    regressed = train._movement(row, {"brest": {"gain_debiased": 0.55}})

    assert "+52.0%" in improved and "+2.0%" in improved and improved.endswith("PASS")
    assert "nouveau" in fresh
    assert "-3.0%" in regressed  # un changement qui dégrade doit se voir au signe


def test_evaluate_is_side_effect_free_until_release(
    tmp_path, monkeypatch
):
    raw = _raw(days=45)
    # `hs_gwam` is deliberately the closest model on every day.
    raw["hs_gwam"] = raw["hs"] + 0.02
    monkeypatch.setattr(train, "DATA_DIR", tmp_path)
    monkeypatch.setattr(model, "MODELS_DIR", tmp_path)
    monkeypatch.setattr(train, "GATE_PATH", tmp_path / "gate.json")
    raw.to_parquet(tmp_path / "synthetic_raw.parquet")

    row = train.evaluate(STATION, test_days=10, model_names=("ridge",))

    assert row is not None
    assert row["baseline_model"] == "gwam"
    assert row["n_test"] > 0 and row["n_train"] > 0
    assert not (tmp_path / "synthetic.joblib").exists()

    gate = train.merge_gate({}, [row], known={STATION.id})
    train.release([row], gate)
    artefact = model.load_artifact("synthetic", models_dir=tmp_path)
    assert artefact["baseline_model"] == "gwam"
    assert artefact["feature_columns"] == WAVE_FEATURE_COLUMNS


def _release_fixture(tmp_path, monkeypatch):
    models_dir = tmp_path / "models"
    gate_path = models_dir / "gate.json"
    models_dir.mkdir()
    monkeypatch.setattr(model, "MODELS_DIR", models_dir)
    monkeypatch.setattr(train, "GATE_PATH", gate_path)

    def fake_stage(estimator, station_id, staging_dir, baseline_model=None):
        path = staging_dir / f"{station_id}.joblib"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(estimator)
        return path

    monkeypatch.setattr(model, "stage", fake_stage)
    rows = [
        {"station": "first", "_estimator": b"new-first", "baseline_model": None},
        {"station": "second", "_estimator": b"new-second", "baseline_model": None},
    ]
    return models_dir, gate_path, rows


def test_release_rolls_back_all_files_when_a_model_promotion_fails(tmp_path, monkeypatch):
    models_dir, gate_path, rows = _release_fixture(tmp_path, monkeypatch)
    first = models_dir / "first.joblib"
    second = models_dir / "second.joblib"
    first.write_bytes(b"old-first")
    second.write_bytes(b"old-second")
    gate_path.write_bytes(b'{"old": true}\n')
    real_replace = model.os.replace
    calls = 0

    def fail_second_replace(source, destination):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected model promotion failure")
        real_replace(source, destination)

    monkeypatch.setattr(model.os, "replace", fail_second_replace)

    with pytest.raises(OSError, match="model promotion"):
        train.release(rows, {"new": {"pass": True, "weak": False}})

    assert first.read_bytes() == b"old-first"
    assert second.read_bytes() == b"old-second"
    assert gate_path.read_bytes() == b'{"old": true}\n'


def test_release_rolls_back_models_when_gate_promotion_fails(tmp_path, monkeypatch):
    models_dir, gate_path, rows = _release_fixture(tmp_path, monkeypatch)
    first = models_dir / "first.joblib"
    second = models_dir / "second.joblib"  # deliberately absent before release
    first.write_bytes(b"old-first")
    gate_path.write_bytes(b'{"old": true}\n')
    real_replace = model.os.replace
    calls = 0

    def fail_gate_replace(source, destination):
        nonlocal calls
        calls += 1
        if calls == 3:
            raise OSError("injected gate promotion failure")
        real_replace(source, destination)

    monkeypatch.setattr(model.os, "replace", fail_gate_replace)

    with pytest.raises(OSError, match="gate promotion"):
        train.release(rows, {"new": {"pass": True, "weak": False}})

    assert first.read_bytes() == b"old-first"
    assert not second.exists()
    assert gate_path.read_bytes() == b'{"old": true}\n'


def test_evaluation_failure_never_releases_an_earlier_station(monkeypatch):
    """A failed second station must leave the first station's live model alone."""
    first = Station(
        id="first", name="First", kind="wave", lat=0, lon=0,
        source="candhis", source_id="first", baseline="marine-best",
    )
    second = Station(
        id="second", name="Second", kind="wave", lat=0, lon=0,
        source="candhis", source_id="second", baseline="marine-best",
    )
    released = []

    def fake_evaluate(station, *_args):
        if station.id == "second":
            raise RuntimeError("station evaluation failed")
        return {"station": station.id, "_estimator": object(), "baseline_model": None}

    monkeypatch.setattr(train, "evaluate", fake_evaluate)
    monkeypatch.setattr(train, "release", lambda rows: released.extend(rows))

    with pytest.raises(RuntimeError, match="station evaluation failed"):
        train.evaluate_all([first, second], None, (), ("ridge",))

    assert released == []


def test_write_report_describes_ecmwf_daily_previous_runs_not_the_old_skew(tmp_path, monkeypatch):
    monkeypatch.setattr(train, "REPORT_PATH", tmp_path / "model-eval.md")
    row = {
        "station": "brest", "kind": "tide", "baseline_model": None,
        "ml_model": "ridge", "n_train": 100, "n_test": 50, "test_days": 365,
        "mae_base": 0.2, "mae_debiased": 0.2, "mae_model": 0.1, "gain": 0.5,
        "gain_debiased": 0.5, "bias": 0.0, "pass": True, "weak": False,
        "events": [], "val_scores": {},
    }

    train.write_report([row])
    report = train.REPORT_PATH.read_text()

    assert "ECMWF" in report
    assert "granularité des Previous\n   Runs reste journalière" in report
    assert "réanalyse ERA5" not in report
    assert "ARPEGE Europe" not in report
    assert "seule feature de forçage restante" not in report


def test_corrupt_gate_aborts_before_any_release(tmp_path, monkeypatch):
    gate_path = tmp_path / "gate.json"
    gate_path.write_text("{not json")
    released = []
    monkeypatch.setattr(train, "GATE_PATH", gate_path)
    monkeypatch.setattr(train, "load_env", lambda: None)
    monkeypatch.setattr(train, "load_stations", lambda: [STATION])
    monkeypatch.setattr(train, "evaluate_all", lambda *_args: [{"station": STATION.id}])
    monkeypatch.setattr(train, "release", lambda rows: released.extend(rows))
    monkeypatch.setattr(sys, "argv", ["train.py"])

    with pytest.raises(json.JSONDecodeError):
        train.main()

    assert released == []


def _gate_row(station_id: str, passes: bool) -> dict:
    return {
        "station": station_id, "baseline_model": None, "pass": passes, "weak": False,
        "mae_model": 0.1, "mae_base": 0.2, "gain": 0.5,
        "gain_debiased": 0.5 if passes else -0.1,
    }


def test_partial_training_requires_a_complete_previous_gate_before_release(tmp_path, monkeypatch):
    other = Station(
        id="other", name="Other", kind="wave", lat=0, lon=0,
        source="candhis", source_id="other", baseline="marine-best",
    )
    released = []
    monkeypatch.setattr(train, "GATE_PATH", tmp_path / "gate.json")  # absent => empty
    monkeypatch.setattr(train, "load_env", lambda: None)
    monkeypatch.setattr(train, "load_stations", lambda: [STATION, other])
    monkeypatch.setattr(train, "evaluate_all", lambda *_args: [_gate_row(STATION.id, True)])
    monkeypatch.setattr(train, "release", lambda rows, gate: released.append((rows, gate)))
    monkeypatch.setattr(sys, "argv", ["train.py", "--station", STATION.id])

    with pytest.raises(ValueError, match="partial training requires"):
        train.main()

    assert released == []


def test_full_training_can_rebuild_a_complete_gate_from_scratch(tmp_path, monkeypatch):
    other = Station(
        id="other", name="Other", kind="wave", lat=0, lon=0,
        source="candhis", source_id="other", baseline="marine-best",
    )
    rows = [_gate_row(STATION.id, True), _gate_row(other.id, False)]
    released = []
    monkeypatch.setattr(train, "GATE_PATH", tmp_path / "gate.json")  # absent => empty
    monkeypatch.setattr(train, "load_env", lambda: None)
    monkeypatch.setattr(train, "load_stations", lambda: [STATION, other])
    monkeypatch.setattr(train, "evaluate_all", lambda *_args: rows)
    monkeypatch.setattr(train, "release", lambda actual, gate: released.append((actual, gate)))
    monkeypatch.setattr(train, "write_report", lambda *_args: None)
    monkeypatch.setattr(sys, "argv", ["train.py"])

    assert train.main() == 0
    assert released[0][0] == rows
    assert set(released[0][1]) == {STATION.id, other.id}
    assert released[0][1][STATION.id]["pass"] is True


def test_inactive_pilot_training_requires_explicit_station_and_keeps_active_scope(
    tmp_path, monkeypatch
):
    pilot = Station(
        id="gascogne-bouee",
        name="Bouée Gascogne",
        kind="wave",
        lat=45.22,
        lon=-5.0,
        source="mfbuoy",
        source_id="6200001",
        baseline="marine-best",
        active=False,
    )
    gate_path = tmp_path / "gate.json"
    gate_path.write_text(json.dumps({STATION.id: {"pass": True, "weak": False}}))
    evaluated = []
    released = []

    monkeypatch.setattr(train, "GATE_PATH", gate_path)
    monkeypatch.setattr(train, "load_env", lambda: None)
    monkeypatch.setattr(
        train,
        "load_stations",
        lambda include_inactive=False: [STATION, pilot] if include_inactive else [STATION],
    )
    monkeypatch.setattr(
        train,
        "evaluate_all",
        lambda stations, *_args: evaluated.extend(stations) or [_gate_row(pilot.id, False)],
    )
    monkeypatch.setattr(train, "release", lambda rows, gate: released.append((rows, gate)))
    monkeypatch.setattr(train, "write_report", lambda *_args: None)
    monkeypatch.setattr(
        sys,
        "argv",
        ["train.py", "--station", pilot.id, "--include-pilots"],
    )

    assert train.main() == 0
    assert [station.id for station in evaluated] == [pilot.id]
    assert evaluated[0].active is False
    assert set(released[0][1]) == {STATION.id, pilot.id}


def test_pilot_opt_in_requires_an_explicit_station(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["train.py", "--include-pilots"])

    with pytest.raises(SystemExit, match="2"):
        train.main()


def test_release_gate_requires_at_least_one_passing_station():
    previous = {
        "first": {"pass": False, "weak": False},
        "second": {"pass": False, "weak": False},
    }

    with pytest.raises(ValueError, match="at least one passing"):
        train.validate_gate_for_release(previous, previous, set(previous), partial=True)


def test_station_filter_never_evicts_the_untrained_stations_from_the_gate(tmp_path, monkeypatch):
    """`--station` restreint ce qu'on ENTRAÎNE, jamais ce que `gate.json` a le
    droit de contenir. Confondre les deux dépublierait en silence toute station
    absente du run — un entraînement ciblé sur une station en retirerait cinq."""
    gate_path = tmp_path / "gate.json"
    previous = {
        "anglet": {"pass": True, "weak": False},
        "brest": {"pass": True, "weak": False},
        "retiree": {"pass": True, "weak": False},  # plus dans stations.toml
    }
    gate_path.write_text(json.dumps(previous))
    monkeypatch.setattr(train, "GATE_PATH", gate_path)

    configured = {"anglet", "brest", "ouessant"}
    rows = [{
        "station": "ouessant", "baseline_model": "icon_eu", "pass": True, "weak": False,
        "mae_model": 1.0, "mae_base": 1.2, "gain": 0.16, "gain_debiased": 0.10,
    }]
    gate = train.merge_gate(json.loads(gate_path.read_text()), rows, known=configured)

    assert gate["anglet"] == previous["anglet"], "station non entraînée : verdict intact"
    assert gate["brest"] == previous["brest"]
    assert gate["ouessant"]["pass"] is True
    assert "retiree" not in gate, "seule la sortie de stations.toml évince une entrée"


def _eval_window(resid: np.ndarray) -> tuple[pd.DataFrame, pd.Series]:
    idx = pd.date_range("2026-01-01", periods=len(resid), freq="h", tz="UTC", name="time")
    baseline = pd.Series(5.0, index=idx)
    return pd.DataFrame({"baseline": baseline}), baseline + resid


def test_event_diagnostic_debiases_on_the_whole_window_not_on_the_band():
    """The band's own mean must never be the offset the baseline gets for free.

    Recomputing the bias inside the storm hours would hand the baseline a
    per-event correction it cannot have in advance, turning the competitor into
    a straw man in the opposite direction. Here 900 calm hours are +2 cm off and
    100 storm hours +40 cm, so the whole-window bias is 5.8 cm and the storm band
    must stay 34.2 cm from a debiased baseline — not the ~0 a band-local
    debiasing would produce.
    """
    resid = np.concatenate([np.full(900, 0.02), np.full(100, 0.40)])
    x_ev, obs_ev = _eval_window(resid)

    # Niveaux d'un modèle à résidu nul : la baseline elle-même.
    events = train._event_scores(x_ev["baseline"].to_numpy(), x_ev, obs_ev)
    storm = next(e for e in events if e["label"] == "|résidu| > 30 cm")

    assert storm["n"] == 100
    whole_window_bias = resid.mean()
    assert storm["mae_debiased"] == pytest.approx(0.40 - whole_window_bias, abs=1e-9)
    # A band-local debiasing would have collapsed this to ~0.
    assert storm["mae_debiased"] > 0.30


def test_event_diagnostic_skips_a_band_too_thin_to_mean_anything():
    """Fewer than a day of qualifying hours yields no row rather than a number
    built on a handful of points."""
    resid = np.concatenate([np.full(1000, 0.01), np.full(5, 0.40)])
    x_ev, obs_ev = _eval_window(resid)

    levels = x_ev["baseline"].to_numpy()
    labels = [e["label"] for e in train._event_scores(levels, x_ev, obs_ev)]

    assert "|résidu| > 30 cm" not in labels
