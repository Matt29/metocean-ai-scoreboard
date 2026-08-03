"""Features + dataset assembly: shape, never-NaN, and the anti-leak guarantee."""

import numpy as np
import pandas as pd
import pytest

from scoreboard.config import Station
from scoreboard.dataset import assemble
from scoreboard.features import FEATURE_COLUMNS, build_features
from scoreboard.sources import SourceError
from scoreboard.sources.wind import FORCING_COLUMNS

T0 = pd.Timestamp("2026-07-30 06:00", tz="UTC")


def _series(start, periods, value, freq="1h"):
    idx = pd.date_range(start, periods=periods, freq=freq, tz="UTC")
    return pd.Series(value, index=idx, dtype=float)


def _baseline(hours_before=24, hours_after=48, value=1.0):
    start = T0 - pd.Timedelta(hours=hours_before)
    return _series(start, hours_before + hours_after + 1, value)


def _forcing(u=3.0, v=-4.0, hours_before=24, hours_after=48, start=None):
    start = T0 - pd.Timedelta(hours=hours_before) if start is None else start
    idx = pd.date_range(start, periods=hours_before + hours_after + 1, freq="1h", tz="UTC")
    return pd.DataFrame({"wind_u10": float(u), "wind_v10": float(v)}, index=idx)


def _empty_forcing():
    return pd.DataFrame(columns=FORCING_COLUMNS, index=pd.DatetimeIndex([], tz="UTC"), dtype=float)


def test_columns_exactly_as_specified():
    feats = build_features(_baseline(), _series(T0 - pd.Timedelta(hours=24), 25, 1.3), T0, _forcing())
    assert list(feats.columns) == FEATURE_COLUMNS
    assert FEATURE_COLUMNS == [
        "baseline",
        "lead_h",
        "last_err",
        "mean_err_24h",
        "hour_sin",
        "hour_cos",
        "wind_u10",
        "wind_v10",
    ]


def test_forcing_is_sampled_at_each_lead_valid_time():
    idx = pd.date_range(T0 - pd.Timedelta(hours=24), periods=73, freq="1h", tz="UTC")
    forcing = pd.DataFrame(
        {"wind_u10": np.arange(73.0), "wind_v10": -np.arange(73.0)}, index=idx
    )
    feats = build_features(_baseline(), _series(T0 - pd.Timedelta(hours=24), 25, 1.3), T0, forcing)

    # lead h maps to index position 24 + h in the forcing frame
    assert np.allclose(feats["wind_u10"], 24 + np.arange(1, 49))
    assert np.allclose(feats["wind_v10"], -(24 + np.arange(1, 49)))


@pytest.mark.parametrize(
    "degraded", [None, "empty", "no_columns", "one_column", "half", "half_one_column"]
)
def test_degraded_forcing_raises_instead_of_silently_zeroing(degraded):
    """A model trained on real forcing must never be served an all-zero vector.

    The guard is per column: one missing or thin column is enough to raise, so a
    forcing variable added later inherits it for free.
    """
    half_v = _forcing()
    half_v.loc[half_v.index > T0 + pd.Timedelta(hours=24), "wind_v10"] = np.nan
    forcing = {
        None: None,
        "empty": _empty_forcing(),
        "no_columns": pd.DataFrame({"gust": [1.0]}, index=pd.DatetimeIndex([T0], tz="UTC")),
        "one_column": _forcing().drop(columns=["wind_v10"]),
        # covers only the first 24h of a 48h horizon -> 50% < _MIN_FORCING_COVERAGE
        "half": _forcing(hours_before=0, hours_after=24, start=T0),
        "half_one_column": half_v,
    }[degraded]
    with pytest.raises(SourceError):
        build_features(_baseline(), _series(T0 - pd.Timedelta(hours=24), 25, 1.3), T0, forcing)


def test_a_few_missing_forcing_hours_are_tolerated_as_neutral_zero():
    forcing = _forcing()
    forcing.iloc[30:33] = np.nan  # 3 leads out of 48 -> above the coverage floor
    feats = build_features(_baseline(), _series(T0 - pd.Timedelta(hours=24), 25, 1.3), T0, forcing)
    assert not feats.isna().any().any()
    # the two edge holes are filled by the 1h-tolerance neighbour; only the
    # middle one has no valid sample within tolerance and falls back to neutral
    assert (feats["wind_u10"] == 0.0).sum() == 1


def test_assemble_skips_issues_whose_forcing_coverage_is_too_thin():
    """A forcing gap drops the affected issue from training — never trains on zeros."""
    obs, baseline, forcing = _history(days=5)
    truncated = forcing[forcing.index < forcing.index[0] + pd.Timedelta(days=3)]
    x, y = assemble(WAVE_STATION, obs, baseline, truncated, issue_hours=[6])
    assert not x.empty
    assert len(x) < len(assemble(WAVE_STATION, obs, baseline, forcing, issue_hours=[6])[0])
    assert not ((x["wind_u10"] == 0.0) & (x["wind_v10"] == 0.0)).all()


def test_constant_case_errors_and_leads():
    obs = _series(T0 - pd.Timedelta(hours=24), 25, 1.3)
    feats = build_features(_baseline(), obs, T0, _forcing())

    assert len(feats) == 48  # one row per hour strictly after t0
    assert feats.index[0] == T0 + pd.Timedelta(hours=1)
    assert list(feats["lead_h"]) == list(range(1, 49))
    assert np.allclose(feats["baseline"], 1.0)
    assert np.allclose(feats["last_err"], 0.3)
    assert np.allclose(feats["mean_err_24h"], 0.3)


def test_hour_encoding_is_cyclic_on_utc_hour():
    feats = build_features(_baseline(), _series(T0 - pd.Timedelta(hours=24), 25, 1.3), T0, _forcing())
    hours = feats.index.hour
    assert np.allclose(feats["hour_sin"], np.sin(2 * np.pi * hours / 24))
    assert np.allclose(feats["hour_cos"], np.cos(2 * np.pi * hours / 24))


def test_empty_obs_history_gives_zeros_not_nan():
    empty = pd.Series(dtype=float, index=pd.DatetimeIndex([], tz="UTC"))
    feats = build_features(_baseline(), empty, T0, _forcing())

    assert len(feats) == 48
    assert (feats["last_err"] == 0.0).all()
    assert (feats["mean_err_24h"] == 0.0).all()
    assert not feats.isna().any().any()


def test_all_nan_obs_history_gives_zeros_not_nan():
    obs = _series(T0 - pd.Timedelta(hours=24), 25, np.nan)
    feats = build_features(_baseline(), obs, T0, _forcing())
    assert not feats.isna().any().any()
    assert (feats["last_err"] == 0.0).all()


def test_no_feature_is_ever_nan_with_gappy_baseline():
    baseline = _baseline()
    baseline.iloc[30] = np.nan  # NaN baseline rows must not leak NaN features
    obs = _series(T0 - pd.Timedelta(hours=24), 25, 1.3)
    feats = build_features(baseline, obs, T0, _forcing())
    assert not feats.isna().any().any()


def test_anti_leak_future_obs_do_not_influence_features():
    """Obs after t0 poisoned at 99.0 must produce byte-identical features."""
    honest = _series(T0 - pd.Timedelta(hours=24), 25, 1.3)  # ends exactly at t0
    poisoned = pd.concat([honest, _series(T0 + pd.Timedelta(hours=1), 48, 99.0)])

    baseline = _baseline()
    clean = build_features(baseline, honest, T0, _forcing())
    dirty = build_features(baseline, poisoned, T0, _forcing())

    pd.testing.assert_frame_equal(clean, dirty)


def test_anti_leak_last_err_uses_last_obs_at_or_before_t0():
    idx = pd.date_range(T0 - pd.Timedelta(hours=3), periods=4, freq="1h", tz="UTC")
    obs = pd.Series([1.3, 1.3, 1.3, 1.9], index=idx)  # 1.9 is exactly at t0 -> usable
    feats = build_features(_baseline(), obs, T0, _forcing())
    assert np.allclose(feats["last_err"], 0.9)

    obs_late = obs.copy()
    obs_late.index = obs_late.index + pd.Timedelta(hours=1)  # 1.9 now lands at t0+1h
    feats_late = build_features(_baseline(), obs_late, T0, _forcing())
    assert np.allclose(feats_late["last_err"], 0.3)


def test_obs_strictly_before_window_still_yields_zero_mean_err():
    old = _series(T0 - pd.Timedelta(days=10), 5, 1.3)  # far outside the 24h window
    feats = build_features(_baseline(), old, T0, _forcing())
    assert (feats["mean_err_24h"] == 0.0).all()
    assert not feats.isna().any().any()


# --- dataset.assemble ---------------------------------------------------

WAVE_STATION = Station(
    id="test-wave",
    name="Test",
    kind="wave",
    lat=48.0,
    lon=-4.0,
    source="candhis",
    source_id="00000",
    baseline="mfwam",
)


def _history(days=5):
    idx = pd.date_range("2026-07-01", periods=days * 24, freq="1h", tz="UTC")
    obs = pd.DataFrame({"hs": np.full(len(idx), 1.3)}, index=idx)
    baseline = pd.DataFrame({"hs_baseline": np.full(len(idx), 1.0)}, index=idx)
    forcing = pd.DataFrame(
        {
            "wind_u10": np.full(len(idx), 3.0),
            "wind_v10": np.full(len(idx), -4.0),
        },
        index=idx,
    )
    return obs, baseline, forcing


def test_assemble_stacks_one_issue_per_day():
    obs, baseline, forcing = _history(days=5)
    x, y = assemble(WAVE_STATION, obs, baseline, forcing, issue_hours=[6])

    assert list(x.columns) == FEATURE_COLUMNS
    assert len(x) == len(y)
    assert not x.isna().any().any()
    assert not y.isna().any()
    assert np.allclose(y, 1.3)
    assert x["lead_h"].min() >= 1
    # 5 days of history, 06 UTC issues, 48h horizon truncated by available data
    assert set(x.index.normalize().unique()).issubset(set(obs.index.normalize().unique()))


def test_assemble_multiple_issue_hours_gives_more_rows():
    obs, baseline, forcing = _history(days=5)
    x1, _ = assemble(WAVE_STATION, obs, baseline, forcing, issue_hours=[6])
    x2, _ = assemble(WAVE_STATION, obs, baseline, forcing, issue_hours=[6, 18])
    assert len(x2) > len(x1)


def test_assemble_target_never_uses_missing_obs():
    obs, baseline, forcing = _history(days=5)
    obs.iloc[50:60] = np.nan
    x, y = assemble(WAVE_STATION, obs, baseline, forcing, issue_hours=[6])
    assert not y.isna().any()
    assert len(x) == len(y)


def test_assemble_empty_history_returns_empty():
    idx = pd.DatetimeIndex([], tz="UTC")
    obs = pd.DataFrame({"hs": []}, index=idx)
    baseline = pd.DataFrame({"hs_baseline": []}, index=idx)
    forcing = pd.DataFrame(columns=FORCING_COLUMNS, index=idx, dtype=float)
    x, y = assemble(WAVE_STATION, obs, baseline, forcing)
    assert x.empty and y.empty
    assert list(x.columns) == FEATURE_COLUMNS


def test_assemble_rejects_unknown_kind():
    bad = Station(**{**WAVE_STATION.__dict__, "kind": "current"})
    obs, baseline, forcing = _history()
    with pytest.raises(ValueError):
        assemble(bad, obs, baseline, forcing)
