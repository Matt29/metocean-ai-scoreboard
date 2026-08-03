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
            "hour_sin": np.sin(2 * np.pi * times.hour / 24),
            "hour_cos": np.cos(2 * np.pi * times.hour / 24),
            "wind_u10": rng.normal(0.0, 5.0, n),
            "wind_v10": rng.normal(0.0, 5.0, n),
            "pressure_anom": rng.normal(0.0, 10.0, n),
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
