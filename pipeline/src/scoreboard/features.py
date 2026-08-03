"""Feature engineering — the single code path used by BOTH training and inference.

Anti-leak contract: `build_features` truncates `obs_recent` at `t0` as its very
first act. Nothing downstream can see an observation posterior to `t0`, whatever
the caller passes in. That structural truncation (not caller discipline) is what
guarantees training and serving see the same thing.

`forcing` (10 m wind, see `sources.wind`) is the one input legitimately allowed
to carry values posterior to `t0`: it is a *forecast* of the atmospheric forcing
at each lead's valid time, exactly what production has at issue time. It is
never an observation, so it cannot leak.
`forcing` is a required argument, not an option with a default — and a required
argument is not enough on its own: a *degraded* forcing frame (None, empty, wrong
columns, truncated horizon) would otherwise yield an all-zero forcing vector,
which is not "neutral" but out-of-distribution for a model trained on real
forcing, and indistinguishable from a genuine calm. So
coverage is checked per column and `SourceError` is raised below
`_MIN_FORCING_COVERAGE`: the daily run marks the station missing and does not
publish it, rather than publishing a silently wrong correction.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from scoreboard.sources import SourceError
from scoreboard.sources.wind import FORCING_COLUMNS as _FORCING_COLUMNS

FEATURE_COLUMNS = [
    "baseline",
    "lead_h",
    "last_err",
    "mean_err_24h",
    "hour_sin",
    "hour_cos",
    # Atmospheric forcing at the lead's valid time (see `sources.wind`).
    # 10 m wind components, m/s, eastward / northward. u/v rather than
    # speed+direction: direction is circular and u/v handle it natively.
    # MSL pressure was tried here in Task 7C and measured non-contributive
    # (see `docs/model-eval.md`) — do not re-add it without new evidence.
    "wind_u10",
    "wind_v10",
]

# 0.0 on every forcing column means calm, i.e. "no atmospheric forcing
# correction" — the neutral fallback, consistent with the never-NaN contract.
# It is only ever reached inside the coverage floor below.
_NEUTRAL_FORCING = 0.0
# Both providers deliver a gap-free hourly grid, so a few missing hours are a
# blip while a third of the horizon missing is a degraded fetch, not weather.
_MIN_FORCING_COVERAGE = 0.9

_ALIGN_TOLERANCE = pd.Timedelta("1h")


def _aligned_baseline(baseline: pd.Series, times: pd.DatetimeIndex) -> pd.Series:
    """Baseline sampled at `times`, nearest hour within 1h (NaN beyond)."""
    if baseline.empty or len(times) == 0:
        return pd.Series(np.nan, index=times, dtype=float)
    return baseline.reindex(times, method="nearest", tolerance=_ALIGN_TOLERANCE)


def _aligned_forcing(forcing: pd.DataFrame, col: str, times: pd.DatetimeIndex) -> np.ndarray:
    """Forcing component at each valid time; raises below `_MIN_FORCING_COVERAGE`."""
    if forcing is None or col not in getattr(forcing, "columns", []):
        raise SourceError("forcing", f"forcing frame missing column {col!r}")
    series = forcing[col].astype(float).dropna().sort_index()
    aligned = series.reindex(times, method="nearest", tolerance=_ALIGN_TOLERANCE)
    coverage = float(aligned.notna().mean()) if len(times) else 1.0
    if coverage < _MIN_FORCING_COVERAGE:
        raise SourceError(
            "forcing", f"{col} covers {coverage:.0%} of the horizon (< {_MIN_FORCING_COVERAGE:.0%})"
        )
    return aligned.fillna(_NEUTRAL_FORCING).to_numpy()


def _finite(value: float) -> float:
    """0.0 rather than NaN — features are never NaN (documented contract)."""
    return 0.0 if value is None or not np.isfinite(value) else float(value)


def build_features(
    baseline: pd.Series, obs_recent: pd.Series, t0: pd.Timestamp, forcing: pd.DataFrame
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
    for col in _FORCING_COLUMNS:
        feats[col] = _aligned_forcing(forcing, col, future.index)
    return feats[FEATURE_COLUMNS]
