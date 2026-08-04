"""Training-set assembly: replay one forecast issue per day over the history."""

from __future__ import annotations

import pandas as pd

from scoreboard.config import Station
from scoreboard.features import FEATURE_COLUMNS, build_features, model_feature_columns
from scoreboard.sources import SourceError

OBS_COLUMN = {"wave": "hs", "tide": "level", "wind": "wind_speed"}
HORIZON_H = 48


def assemble(
    station: Station,
    obs: pd.DataFrame,
    baseline: pd.DataFrame,
    forcing: pd.DataFrame,
    issue_hours: list[int] | None = None,
    models: pd.DataFrame | None = None,
) -> tuple[pd.DataFrame, pd.Series]:
    """Stack (X, y) over simulated daily issues at `issue_hours` UTC.

    `obs` is the station's observation frame (hourly); `baseline` holds the
    official forecast / harmonic prediction in its first column; `forcing` holds
    the hourly `wind_u10`/`wind_v10` columns, or the run-stratified wide frame a
    tide station trains on (see `sources.wind.forcing_at_issue`). Target is the
    observation at the same valid time. `models` (multi-model stations) is
    passed through to `build_features` verbatim — see its docstring.
    """
    if station.kind not in OBS_COLUMN:
        raise ValueError(f"unsupported station kind: {station.kind!r}")
    issue_hours = [6] if issue_hours is None else issue_hours

    obs_s = obs[OBS_COLUMN[station.kind]].astype(float).dropna().sort_index()
    base_s = baseline.iloc[:, 0].astype(float).dropna().sort_index()

    columns = (
        model_feature_columns(list(models.columns)) if models is not None else FEATURE_COLUMNS
    )
    empty = (pd.DataFrame(columns=columns), pd.Series(dtype=float))
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
            try:
                feats = build_features(window, obs_s, t0, forcing, models=models)
            except SourceError:
                continue  # forcing gap on this issue: drop it rather than train on zeros
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
