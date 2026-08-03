"""Thin wrapper around utide.solve/reconstruct — astronomical tide baseline."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import joblib
import pandas as pd
import utide


@dataclass
class HarmonicModel:
    coef: object  # utide.Bunch, opaque solution container

    def predict(self, times: pd.DatetimeIndex) -> pd.Series:
        recon = utide.reconstruct(times, self.coef, verbose=False)
        return pd.Series(recon.h, index=times)

    def save(self, path: str | Path) -> None:
        joblib.dump(self.coef, path)

    @classmethod
    def load(cls, path: str | Path) -> "HarmonicModel":
        return cls(coef=joblib.load(path))


def fit(obs: pd.Series, lat: float) -> HarmonicModel:
    coef = utide.solve(obs.index, obs.values, lat=lat, verbose=False)
    return HarmonicModel(coef=coef)


def causal_predict(
    obs: pd.Series,
    lat: float,
    times: pd.DatetimeIndex,
    first_cutoff: pd.Timestamp,
    refit_days: int = 30,
    horizon_hours: int = 48,
) -> pd.Series:
    """Harmonic baseline over `times`, refitted every `refit_days` on past obs only.

    Anti-leak contract, structural like `features.build_features`: the model
    serving a valid time `v` is fitted on observations *strictly before* a cutoff
    `c <= v - horizon_hours`. Since a forecast issued at `t0` only covers
    `v <= t0 + horizon_hours`, that cutoff is always `<= t0` — no observation
    posterior to the issue can reach the fit, whatever the caller passes in.

    Consequence: values before `first_cutoff + horizon_hours` cannot be served
    causally and are absent from the returned series.
    """
    times = pd.DatetimeIndex(times).sort_values()
    horizon = pd.Timedelta(hours=horizon_hours)
    step = pd.Timedelta(days=refit_days)

    cutoffs = []
    cutoff = pd.Timestamp(first_cutoff)
    while len(times) and cutoff + horizon <= times[-1]:
        cutoffs.append(cutoff)
        cutoff += step

    parts = []
    for i, cutoff in enumerate(cutoffs):
        lo = cutoff + horizon
        hi = cutoffs[i + 1] + horizon if i + 1 < len(cutoffs) else None
        chunk = times[(times >= lo)] if hi is None else times[(times >= lo) & (times < hi)]
        past = obs[obs.index < cutoff].dropna()
        if len(chunk) == 0 or past.empty:
            continue
        parts.append(fit(past, lat).predict(chunk))
    return pd.concat(parts) if parts else pd.Series(dtype=float, index=times[:0])
