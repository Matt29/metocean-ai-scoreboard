"""train.py: baseline selection (no test leak), per-lead router, artefact keys."""

from __future__ import annotations

import importlib.util
import json
import sys
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
    """The published candidate is chosen on validation, and its reported score
    is a single test evaluation — not the best of three test scores."""
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

    # 2 validation scores + exactly 1 test score, and the published numbers are
    # that last one — the test window is evaluated once, by the winner alone.
    assert len(seen) == 3
    assert seen[-1] == row["n_test"]
    assert seen[0] == seen[1] == row["n_val"] != row["n_test"]
    assert row["ml_model"] in ("ridge", "hgb")
    assert set(row["val_scores"]) == {"ridge", "hgb"}


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


def test_evaluate_writes_an_artefact_carrying_baseline_model_and_feature_columns(
    tmp_path, monkeypatch
):
    raw = _raw(days=45)
    # `hs_gwam` is deliberately the closest model on every day.
    raw["hs_gwam"] = raw["hs"] + 0.02
    monkeypatch.setattr(train, "DATA_DIR", tmp_path)
    monkeypatch.setattr(model, "MODELS_DIR", tmp_path)
    raw.to_parquet(tmp_path / "synthetic_raw.parquet")

    row = train.evaluate(STATION, test_days=10, model_names=("ridge",))

    assert row is not None
    assert row["baseline_model"] == "gwam"
    assert row["n_test"] > 0 and row["n_train"] > 0
    artefact = model.load_artifact("synthetic", models_dir=tmp_path)
    assert artefact["baseline_model"] == "gwam"
    assert artefact["feature_columns"] == WAVE_FEATURE_COLUMNS


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
