"""Model contract: learn a known baseline correction, and round-trip on disk."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from scoreboard import model
from scoreboard.features import FEATURE_COLUMNS


def _synthetic(n: int = 2000, seed: int = 0) -> tuple[pd.DataFrame, pd.Series]:
    """y = baseline + 0.5 * last_err — a correction the model must recover."""
    rng = np.random.default_rng(seed)
    times = pd.date_range("2026-01-01", periods=n, freq="h", tz="UTC")
    x = pd.DataFrame(
        {
            "baseline": rng.uniform(0.5, 5.0, n),
            "lead_h": rng.integers(1, 49, n),
            "last_err": rng.normal(0.0, 0.4, n),
            "mean_err_24h": rng.normal(0.0, 0.3, n),
            "mean_err_3h": rng.normal(0.0, 0.3, n),
            "mean_err_6h": rng.normal(0.0, 0.3, n),
            "hour_sin": np.sin(2 * np.pi * times.hour / 24),
            "hour_cos": np.cos(2 * np.pi * times.hour / 24),
            "tide_rate": rng.normal(0.0, 1.0, n),
            "wind_u10": rng.normal(0.0, 5.0, n),
            "wind_v10": rng.normal(0.0, 5.0, n),
            "pressure_anom": rng.normal(0.0, 8.0, n),
            "dp_dt_3h": rng.normal(0.0, 0.5, n),
            "dp_dt_6h": rng.normal(0.0, 0.5, n),
        },
        index=times,
    )[FEATURE_COLUMNS]
    y = x["baseline"] + 0.5 * x["last_err"]
    return x, y


def test_beats_raw_baseline_on_a_learnable_correction():
    x, y = _synthetic()
    split = len(x) // 2
    m = model.train(x.iloc[:split], y.iloc[:split])

    x_test, y_test = x.iloc[split:], y.iloc[split:]
    mae_model = np.abs(model.predict(m, x_test) - y_test).mean()
    mae_baseline = np.abs(x_test["baseline"] - y_test).mean()

    assert mae_model < mae_baseline * 0.5


def test_save_load_round_trip_is_identical(tmp_path):
    x, y = _synthetic(n=500)
    m = model.train(x, y)

    path = model.save(m, "test-station", models_dir=tmp_path)
    assert path.exists()
    reloaded = model.load("test-station", models_dir=tmp_path)

    np.testing.assert_array_equal(model.predict(m, x), model.predict(reloaded, x))


def test_staged_artefact_is_not_live_until_promoted(tmp_path):
    x, y = _synthetic(n=500)
    fitted = model.train(x, y)
    live = tmp_path / "live"
    staged = model.stage(fitted, "test-station", tmp_path / "staging")

    assert staged.exists()
    assert not (live / "test-station.joblib").exists()

    destination = live / "test-station.joblib"
    model.promote_transaction([(staged, destination)], tmp_path / "backups")
    assert destination.exists()
    np.testing.assert_array_equal(
        model.predict(fitted, x), model.predict(model.load("test-station", live), x)
    )


def test_predict_rejects_missing_features():
    x, y = _synthetic(n=200)
    m = model.train(x, y)
    with pytest.raises(ValueError, match="last_err"):
        model.predict(m, x.drop(columns=["last_err"]))


def test_predict_is_order_insensitive():
    x, y = _synthetic(n=200)
    m = model.train(x, y)
    shuffled = x[list(reversed(FEATURE_COLUMNS))]
    np.testing.assert_array_equal(model.predict(m, x), model.predict(m, shuffled))


@pytest.mark.parametrize("name", model.MODEL_NAMES)
def test_infer_model_name_is_exact_on_each_candidate(name):
    x, y = _synthetic(n=200)
    m = model.train(x, y, name=name)
    assert model.infer_model_name(m) == name


def test_infer_model_name_is_none_on_an_unknown_structure():
    assert model.infer_model_name(object()) is None


def test_save_writes_model_name_and_load_artifact_reads_it_back(tmp_path):
    x, y = _synthetic(n=200)
    m = model.train(x, y, name="ridge")
    model.save(m, "test-station", models_dir=tmp_path)

    artifact = model.load_artifact("test-station", models_dir=tmp_path)
    assert artifact["model_name"] == "ridge"


def test_load_artifact_falls_back_to_inference_on_a_legacy_dict(tmp_path):
    """An artefact dict written before `model_name` existed still gets it, by
    structural inference — no retraining needed."""
    import joblib

    x, y = _synthetic(n=200)
    m = model.train(x, y, name="hgb-per-lead")
    path = tmp_path / "legacy.joblib"
    joblib.dump({"model": m, "baseline_model": None, "feature_columns": list(x.columns)}, path)

    artifact = model.load_artifact("legacy", models_dir=tmp_path)
    assert artifact["model_name"] == "hgb-per-lead"


def test_save_serializes_the_caller_name_not_a_structural_reinference(tmp_path):
    """`train(..., name=...)` is the source of truth for `_dump`: even if the
    fitted estimator's structure would infer a *different* candidate, the
    name the caller actually asked for is what gets serialized."""
    x, y = _synthetic(n=200)
    m = model.train(x, y, name="hgb")
    assert model.infer_model_name(m) == "hgb"  # structure alone says "hgb"
    m._model_name = "ridge"  # simulate a caller-declared name diverging from structure

    model.save(m, "test-station", models_dir=tmp_path)
    artifact = model.load_artifact("test-station", models_dir=tmp_path)
    assert artifact["model_name"] == "ridge"


def test_load_artifact_falls_back_to_inference_on_a_bare_estimator(tmp_path):
    """Pre-Task-5 artefacts are a bare estimator, not a dict; `model_name`
    still comes back via structural inference."""
    import joblib

    x, y = _synthetic(n=200)
    m = model.train(x, y, name="hgb")
    path = tmp_path / "bare.joblib"
    joblib.dump(m, path)

    artifact = model.load_artifact("bare", models_dir=tmp_path)
    assert artifact["model_name"] == "hgb"
