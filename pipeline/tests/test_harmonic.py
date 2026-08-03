import numpy as np
import pandas as pd
import pytest

from scoreboard.harmonic import causal_predict, fit

LAT = 48.38


def _tide(index: pd.DatetimeIndex, origin: pd.Timestamp) -> pd.Series:
    hours = (index - origin) / pd.Timedelta(hours=1)
    return pd.Series(2.0 * np.sin(2 * np.pi * hours / 12.42), index=index)


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


def test_causal_predict_ignores_observations_after_the_issue():
    """Obs poisoned from `cut` on must not move the baseline serving issues <= `cut`."""
    obs_index = pd.date_range("2026-01-01", periods=250 * 24, freq="h", tz="UTC")
    obs = _tide(obs_index, obs_index[0])
    first_cutoff = pd.Timestamp("2026-03-01", tz="UTC")
    times = pd.date_range("2026-03-01", "2026-09-01", freq="h", tz="UTC")
    cut = pd.Timestamp("2026-06-01", tz="UTC")

    poisoned = obs.copy()
    poisoned[poisoned.index >= cut] += 100.0

    clean_pred = causal_predict(obs, LAT, times, first_cutoff=first_cutoff)
    poisoned_pred = causal_predict(poisoned, LAT, times, first_cutoff=first_cutoff)

    # Everything a forecast issued at or before `cut` can cover (t0 + 48h max).
    served = clean_pred.index <= cut + pd.Timedelta(hours=48)
    assert served.any()
    pd.testing.assert_series_equal(clean_pred[served], poisoned_pred[served])
    # Sanity: far enough after the poison, the refit does pick it up.
    late = clean_pred.index > cut + pd.Timedelta(days=60)
    assert np.abs(clean_pred[late] - poisoned_pred[late]).mean() > 10.0



def test_causal_predict_rejects_a_non_advancing_refit_cadence():
    """`refit_days=0` would grow the cutoff list forever — fail fast instead."""
    index = pd.date_range("2026-01-01", periods=60 * 24, freq="h", tz="UTC")
    obs = _tide(index, index[0])
    with pytest.raises(ValueError, match="refit_days"):
        causal_predict(obs, LAT, index, first_cutoff=index[0], refit_days=0)
