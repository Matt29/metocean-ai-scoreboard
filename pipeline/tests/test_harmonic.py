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
    # La date du fit voyage avec les coefficients : c'est elle, et pas la date
    # d'écriture du fichier, qui dit à `daily` si l'artefact est périmé.
    assert loaded.fitted_at == t[-1]


def test_fit_does_not_extrapolate_the_secular_trend():
    """Cicatrice du module : utide extrapole la tendance qu'il a estimée, et un
    fit servi pendant des mois la propage — un fit de 90 j gelé avait porté un
    offset de -0,3 m. Sur une série qui monte de 1 m/an, la prédiction à +180 j
    doit rester centrée, pas suivre la rampe."""
    t = pd.date_range("2026-01-01", periods=400 * 24, freq="h", tz="UTC")
    hours = (t - t[0]) / pd.Timedelta(hours=1)
    obs = pd.Series(2.0 * np.sin(2 * np.pi * hours / 12.42) + hours / (365 * 24), index=t)

    future = pd.date_range(t[-1] + pd.Timedelta(days=180), periods=24 * 30, freq="h", tz="UTC")
    drift = fit(obs, LAT).predict(future).mean() - obs.mean()

    assert abs(drift) < 0.10  # la rampe extrapolée vaudrait ~+0,7 m


def test_causal_predict_ignores_observations_after_the_issue():
    """Obs poisoned from `cut` on must not move the baseline serving issues <= `cut`."""
    obs_index = pd.date_range("2026-01-01", periods=250 * 24, freq="h", tz="UTC")
    obs = _tide(obs_index, obs_index[0])
    first_cutoff = pd.Timestamp("2026-03-01", tz="UTC")
    times = pd.date_range("2026-03-01", "2026-09-01", freq="h", tz="UTC")
    cut = pd.Timestamp("2026-06-01", tz="UTC")

    poisoned = obs.copy()
    poisoned[poisoned.index >= cut] += 100.0

    # Cadence explicite et plus courte que `REFIT_DAYS` : le contrat anti-fuite
    # ne dépend pas d'elle, mais la deuxième moitié du test (le refit finit par
    # voir le poison) a besoin d'un refit dans la fenêtre de 6 mois évaluée.
    clean_pred = causal_predict(obs, LAT, times, first_cutoff=first_cutoff, refit_days=30)
    poisoned_pred = causal_predict(poisoned, LAT, times, first_cutoff=first_cutoff, refit_days=30)

    # Everything a forecast issued at or before `cut` can cover (t0 + 48h max).
    served = clean_pred.index <= cut + pd.Timedelta(hours=48)
    assert served.any()
    pd.testing.assert_series_equal(clean_pred[served], poisoned_pred[served])
    # Sanity: far enough after the poison, the refit does pick it up.
    late = clean_pred.index > cut + pd.Timedelta(days=60)
    assert np.abs(clean_pred[late] - poisoned_pred[late]).mean() > 10.0



def test_causal_predict_fit_window_slides_instead_of_expanding():
    """Obs older than `lookback_days` must not reach the fit.

    The daily run can only fetch a bounded window, so a backtest fitting on the
    expanding history would score a baseline better than the served one. Poison
    the oldest observations: inside the bound they are invisible, outside it they
    move the baseline — the second half is what gives this test teeth.
    """
    index = pd.date_range("2026-01-01", periods=300 * 24, freq="h", tz="UTC")
    obs = _tide(index, index[0])
    first_cutoff = index[0] + pd.Timedelta(days=200)
    times = pd.date_range(first_cutoff, first_cutoff + pd.Timedelta(days=60), freq="h", tz="UTC")

    poisoned = obs.copy()
    poisoned[poisoned.index < index[0] + pd.Timedelta(days=90)] += 100.0

    def run(series, lookback):
        return causal_predict(
            series, LAT, times, first_cutoff=first_cutoff,
            refit_days=1000, lookback_days=lookback,  # one cutoff, keeps the test cheap
        )

    # Window [cutoff-100d, cutoff) = days 100..200 — entirely after the poison.
    bounded_clean, bounded_poisoned = run(obs, 100), run(poisoned, 100)
    assert not bounded_clean.empty
    pd.testing.assert_series_equal(bounded_clean, bounded_poisoned)

    # Window [cutoff-250d, cutoff) reaches back into it, so the fit must move.
    assert np.abs(run(obs, 250) - run(poisoned, 250)).mean() > 1.0


def test_causal_predict_rejects_a_non_advancing_lookback():
    """`lookback_days=0` would fit on an empty window — fail fast instead."""
    index = pd.date_range("2026-01-01", periods=60 * 24, freq="h", tz="UTC")
    obs = _tide(index, index[0])
    with pytest.raises(ValueError, match="lookback_days"):
        causal_predict(obs, LAT, index, first_cutoff=index[0], lookback_days=0)


def test_causal_predict_rejects_a_non_advancing_refit_cadence():
    """`refit_days=0` would grow the cutoff list forever — fail fast instead."""
    index = pd.date_range("2026-01-01", periods=60 * 24, freq="h", tz="UTC")
    obs = _tide(index, index[0])
    with pytest.raises(ValueError, match="refit_days"):
        causal_predict(obs, LAT, index, first_cutoff=index[0], refit_days=0)
