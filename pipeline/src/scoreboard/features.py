"""Feature engineering — the single code path used by BOTH training and inference.

Anti-leak contract: `build_features` truncates `obs_recent` at `t0` as its very
first act. Nothing downstream can see an observation posterior to `t0`, whatever
the caller passes in. That structural truncation (not caller discipline) is what
guarantees training and serving see the same thing.

`wind` is the one input legitimately allowed to carry values posterior to `t0`:
it is a *forecast* of the atmospheric forcing at each lead's valid time, exactly
what production has at issue time. It is never an observation, so it cannot leak.
`wind` is a required argument, not an option with a default: a caller that
silently omitted it would ship a train/serve skew, which is the failure mode
this whole module exists to prevent.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

FEATURE_COLUMNS = [
    "baseline",
    "lead_h",
    "last_err",
    "mean_err_24h",
    "hour_sin",
    "hour_cos",
    # 10 m wind components at the lead's valid time (m/s, eastward / northward).
    # u/v rather than speed+direction: direction is circular and u/v handle it
    # natively. Neutral value when the wind is missing: 0.0 = calm, i.e. "no
    # atmospheric forcing correction", consistent with the never-NaN contract.
    "wind_u10",
    "wind_v10",
]

_NEUTRAL_WIND = 0.0

_ALIGN_TOLERANCE = pd.Timedelta("1h")


def _aligned_baseline(baseline: pd.Series, times: pd.DatetimeIndex) -> pd.Series:
    """Baseline sampled at `times`, nearest hour within 1h (NaN beyond)."""
    if baseline.empty or len(times) == 0:
        return pd.Series(np.nan, index=times, dtype=float)
    return baseline.reindex(times, method="nearest", tolerance=_ALIGN_TOLERANCE)


def _aligned_wind(wind: pd.DataFrame, col: str, times: pd.DatetimeIndex) -> np.ndarray:
    """Wind component sampled at each valid time; `_NEUTRAL_WIND` where unavailable."""
    if wind is None or wind.empty or col not in wind.columns:
        return np.full(len(times), _NEUTRAL_WIND)
    series = wind[col].astype(float).dropna().sort_index()
    if series.empty:
        return np.full(len(times), _NEUTRAL_WIND)
    return series.reindex(times, method="nearest", tolerance=_ALIGN_TOLERANCE).fillna(
        _NEUTRAL_WIND
    ).to_numpy()


def _finite(value: float) -> float:
    """0.0 rather than NaN — features are never NaN (documented contract)."""
    return 0.0 if value is None or not np.isfinite(value) else float(value)


def build_features(
    baseline: pd.Series, obs_recent: pd.Series, t0: pd.Timestamp, wind: pd.DataFrame
) -> pd.DataFrame:
    """One row per baseline hour strictly after `t0`, columns `FEATURE_COLUMNS`."""
    baseline = baseline.dropna().sort_index()
    # Anti-leak: everything after t0 is discarded before any feature is computed.
    past_obs = obs_recent[obs_recent.index <= t0].dropna().sort_index()

    if past_obs.empty:
        last_err = 0.0
        mean_err_24h = 0.0
    else:
        t_last = past_obs.index[-1]
        b_last = _aligned_baseline(baseline, pd.DatetimeIndex([t_last])).iloc[0]
        last_err = _finite(past_obs.iloc[-1] - b_last)

        window = past_obs[past_obs.index > t0 - pd.Timedelta(hours=24)]
        errs = window - _aligned_baseline(baseline, window.index)
        mean_err_24h = _finite(errs.mean()) if len(errs) else 0.0

    future = baseline[baseline.index > t0]
    feats = pd.DataFrame(index=future.index)
    feats.index.name = "time"
    feats["baseline"] = future.astype(float).values
    feats["lead_h"] = ((future.index - t0) / pd.Timedelta(hours=1)).to_numpy().round().astype(int)
    feats["last_err"] = last_err
    feats["mean_err_24h"] = mean_err_24h
    feats["hour_sin"] = np.sin(2 * np.pi * future.index.hour / 24)
    feats["hour_cos"] = np.cos(2 * np.pi * future.index.hour / 24)
    for col in ("wind_u10", "wind_v10"):
        feats[col] = _aligned_wind(wind, col, future.index)
    return feats[FEATURE_COLUMNS]
