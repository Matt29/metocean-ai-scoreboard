"""Feature engineering — the single code path used by BOTH training and inference.

Anti-leak contract: `build_features` truncates `obs_recent` at `t0` as its very
first act. Nothing downstream can see an observation posterior to `t0`, whatever
the caller passes in. That structural truncation (not caller discipline) is what
guarantees training and serving see the same thing.

`forcing` (10 m wind, see `sources.wind`) is the one input legitimately allowed
to carry values posterior to `t0`: it is a *forecast* of the atmospheric forcing
at each lead's valid time, exactly what production has at issue time. It is
never an observation, so it cannot leak.
On the tide path `forcing` may arrive *run-stratified* — one block of columns
per lead day — and `build_features` narrows it to the run an issue at `t0` could
really have had. Serve and train hand it the same shape they each have; the
narrowing lives here so neither has to remember it.

`forcing` is a required argument, not an option with a default — and a required
argument is not enough on its own: a *degraded* forcing frame (None, empty, wrong
columns, truncated horizon) would otherwise yield an all-zero forcing vector,
which is not "neutral" but out-of-distribution for a model trained on real
forcing, and indistinguishable from a genuine calm. So
coverage is checked per column and `SourceError` is raised below
`_MIN_FORCING_COVERAGE`: the daily run marks the station missing and does not
publish it, rather than publishing a silently wrong correction.

`models` (optional, multi-model stations) is the same kind of input as
`forcing`: a *forecast* of the observed quantity by several physical models at
each lead's valid time — Hs by the 5 wave models for `kind="wave"`, 10 m wind
speed by the 3 atmospheric models for `kind="wind"`. Like `forcing` it may
legitimately carry values posterior to `t0` and is never truncated; unlike
`obs_recent` it cannot leak. Passing it switches the frame to
`model_feature_columns(models.columns)` (per-model value + their spread +
multi-model wind), and the forcing frame is then read on
`MULTI_FORCING_COLUMNS`. Passing nothing leaves the tide path exactly as it was.

The column list is taken from the frame itself rather than from a per-kind
constant: that is what makes the wave and wind paths **one** code path instead
of two, and one code path is this project's central guarantee against
train/serve skew.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from scoreboard.sources import SourceError
from scoreboard.sources.marine import MODEL_COLUMNS
from scoreboard.sources.wind import MULTI_FORCING_COLUMNS, WIND_MODEL_COLUMNS, forcing_at_issue
from scoreboard.sources.wind import TIDE_FORCING_COLUMNS as _TIDE_FORCING_COLUMNS

# The 6 columns every station shares, whatever its kind. Named rather than
# sliced off the end of FEATURE_COLUMNS: that negative slice silently leaked any
# forcing column added to the tide list into the wave/wind lists.
BASE_COLUMNS = [
    "baseline",
    "lead_h",
    "last_err",
    "mean_err_24h",
    "hour_sin",
    "hour_cos",
]

# Tide stations: atmospheric forcing at the lead's valid time (see
# `sources.wind`). 10 m wind components, m/s, eastward / northward — u/v rather
# than speed+direction, because direction is circular and u/v handle it
# natively — plus the MSL pressure anomaly, whose inverse barometer drives the
# surge these stations actually predict.
FEATURE_COLUMNS = BASE_COLUMNS + _TIDE_FORCING_COLUMNS


def model_feature_columns(model_columns: list[str]) -> list[str]:
    """Multi-model stations: the same 6 leading columns, then one column per
    physical model, their row-wise dispersion (a cheap uncertainty proxy), then
    the wind of each candidate atmospheric model instead of the single one.

    No pressure here: Task 7C measured it costing 1 to 5 points on every `wave`
    station, and a wave height has no inverse-barometer response to begin with."""
    return BASE_COLUMNS + list(model_columns) + ["model_spread"] + MULTI_FORCING_COLUMNS


WAVE_FEATURE_COLUMNS = model_feature_columns(MODEL_COLUMNS)
WIND_FEATURE_COLUMNS = model_feature_columns(WIND_MODEL_COLUMNS)

# 0.0 on every forcing column means calm, i.e. "no atmospheric forcing
# correction" — the neutral fallback, consistent with the never-NaN contract.
# It is only ever reached inside the coverage floor below.
_NEUTRAL_FORCING = 0.0
# Both providers deliver a gap-free hourly grid, so a few missing hours are a
# blip while a third of the horizon missing is a degraded fetch, not weather.
_MIN_FORCING_COVERAGE = 0.9
# Model columns only: hours of hole a linear-in-time interpolation may bridge.
# A sea state barely moves over 3 h; beyond that we would be inventing a swell.
# Same bound kept for wind speed — 3 h is already generous for a gust-scale
# quantity, and widening it would fabricate the very weather being measured.
_MAX_MODEL_GAP = 3

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


def _add_aligned(feats: pd.DataFrame, frame: pd.DataFrame, cols: list[str]) -> None:
    """Add each of `cols` from `frame`, aligned on the feature index and guarded."""
    for col in cols:
        feats[col] = _aligned_forcing(frame, col, feats.index)


def _finite(value: float) -> float:
    """0.0 rather than NaN — features are never NaN (documented contract)."""
    return 0.0 if value is None or not np.isfinite(value) else float(value)


def build_features(
    baseline: pd.Series,
    obs_recent: pd.Series,
    t0: pd.Timestamp,
    forcing: pd.DataFrame,
    models: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """One row per baseline hour strictly after `t0`.

    Columns `FEATURE_COLUMNS`, or `model_feature_columns(models.columns)` when
    `models` is given (a multi-model forecast frame, see the module docstring).
    """
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
    if models is None:
        # Narrowed here and nowhere else. A tide frame may be *run-stratified*
        # (one block per lead day, see `sources.wind.forcing_at_issue`), and
        # picking the block an issue at `t0` could have had is precisely "how do
        # I read forcing at t0" — this function's job, not its callers'. Doing it
        # at the call sites would make the one-code-path guarantee a convention
        # two callers have to remember instead of a property of the code.
        _add_aligned(feats, forcing_at_issue(forcing, t0), _TIDE_FORCING_COLUMNS)
        return feats[FEATURE_COLUMNS]

    # Each model gets the forcing treatment: nearest-hour alignment and the same
    # 90% coverage floor, so a dead model raises instead of being served as flat
    # water. Short holes are interpolated in time first, because the 0.0 fallback
    # is wrong for a model column: 0 m of sea does not exist, and an isolated zero
    # would inflate `model_spread` exactly where the data is missing. The wind
    # forcing below keeps the 0.0 fill — a 0 m/s wind is an ordinary calm. Holes
    # longer than `_MAX_MODEL_GAP` stay NaN and are caught by the coverage floor.
    model_columns = list(models.columns)
    _add_aligned(
        feats,
        models.sort_index().interpolate(method="time", limit=_MAX_MODEL_GAP),
        model_columns,
    )
    # Vectorised `_finite`: the guard above already forecloses NaN, kept because
    # "a feature is never NaN" is a contract, not an inference from the caller.
    feats["model_spread"] = np.nan_to_num(
        feats[model_columns].to_numpy().std(axis=1), nan=0.0, posinf=0.0, neginf=0.0
    )
    _add_aligned(feats, forcing, MULTI_FORCING_COLUMNS)
    return feats[model_feature_columns(model_columns)]
