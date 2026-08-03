"""Training-set assembly: replay one forecast issue per day over the history."""

from __future__ import annotations

import pandas as pd

from scoreboard.config import Station
from scoreboard.features import FEATURE_COLUMNS, build_features

OBS_COLUMN = {"wave": "hs", "tide": "level"}
HORIZON_H = 48


def assemble(
    station: Station,
    obs: pd.DataFrame,
    baseline: pd.DataFrame,
    wind: pd.DataFrame,
    issue_hours: list[int] | None = None,
) -> tuple[pd.DataFrame, pd.Series]:
    """Stack (X, y) over simulated daily issues at `issue_hours` UTC.

    `obs` is the station's observation frame (hourly); `baseline` holds the
    official forecast / harmonic prediction in its first column; `wind` holds
    the hourly `wind_u10`/`wind_v10` columns (see `sources.wind`). Target is the
    observation at the same valid time.
    """
    if station.kind not in OBS_COLUMN:
        raise ValueError(f"unsupported station kind: {station.kind!r}")
    issue_hours = [6] if issue_hours is None else issue_hours

    obs_s = obs[OBS_COLUMN[station.kind]].astype(float).dropna().sort_index()
    base_s = baseline.iloc[:, 0].astype(float).dropna().sort_index()

    empty = (pd.DataFrame(columns=FEATURE_COLUMNS), pd.Series(dtype=float))
    if obs_s.empty or base_s.empty:
        return empty

    days = pd.date_range(base_s.index[0].normalize(), base_s.index[-1].normalize(), freq="D")
    frames, targets = [], []
    for day in days:
        for hour in issue_hours:
            t0 = day + pd.Timedelta(hours=hour)
            # Baseline slice: 24h of history (for mean_err_24h) + the forecast horizon.
            window = base_s[
                (base_s.index > t0 - pd.Timedelta(hours=24))
                & (base_s.index <= t0 + pd.Timedelta(hours=HORIZON_H))
            ]
            if window.empty:
                continue
            feats = build_features(window, obs_s, t0, wind)
            if feats.empty:
                continue
            y = obs_s.reindex(feats.index)
            keep = y.notna()
            if not keep.any():
                continue
            frames.append(feats[keep.values])
            targets.append(y[keep])

    if not frames:
        return empty

    x = pd.concat(frames)
    y = pd.concat(targets)
    return x, y
