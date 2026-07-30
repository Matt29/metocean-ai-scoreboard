import numpy as np
import pandas as pd

from scoreboard.harmonic import fit


def test_fit_predicts_m2_signal():
    lat = 48.38
    t = pd.date_range("2026-01-01", periods=30 * 24, freq="h", tz="UTC")
    hours = (t - t[0]) / pd.Timedelta(hours=1)
    obs = pd.Series(2.0 * np.sin(2 * np.pi * hours / 12.42), index=t)

    model = fit(obs, lat)

    future = pd.date_range(t[-1] + pd.Timedelta(hours=1), periods=24, freq="h", tz="UTC")
    pred = model.predict(future)

    future_hours = (future - t[0]) / pd.Timedelta(hours=1)
    exact = 2.0 * np.sin(2 * np.pi * future_hours / 12.42)

    corr = np.corrcoef(pred.values, exact)[0, 1]
    assert corr > 0.99


def test_save_load_roundtrip(tmp_path):
    lat = 48.38
    t = pd.date_range("2026-01-01", periods=30 * 24, freq="h", tz="UTC")
    hours = (t - t[0]) / pd.Timedelta(hours=1)
    obs = pd.Series(2.0 * np.sin(2 * np.pi * hours / 12.42), index=t)
    model = fit(obs, lat)

    path = tmp_path / "model.joblib"
    model.save(path)

    from scoreboard.harmonic import HarmonicModel
    loaded = HarmonicModel.load(path)

    future = pd.date_range(t[-1] + pd.Timedelta(hours=1), periods=24, freq="h", tz="UTC")
    pd.testing.assert_series_equal(model.predict(future), loaded.predict(future))
