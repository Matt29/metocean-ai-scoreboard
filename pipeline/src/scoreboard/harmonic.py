"""Thin wrapper around utide.solve/reconstruct — astronomical tide baseline."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import joblib
import pandas as pd
import utide

# How much past observation one tidal analysis is allowed to see. One year, and
# the number is physical, not a tuning knob: two constituents are only separable
# over a record longer than the inverse of their frequency gap (Rayleigh). Below
# ~182 days utide cannot separate S2/K2 nor K1/P1 and *infers* them from fixed
# admittance ratios instead of solving them; below ~365 days the annual Sa (and
# with it the seasonal mean-sea-level swing, ~5-10 cm at Brest) is not in the
# basis at all and leaks into the fitted mean — a bias that then drifts with the
# season. This constant is the single source of truth for both the training
# backtest (`causal_predict`) and the daily run (`daily.TIDE_FIT_LOOKBACK_DAYS`):
# a baseline fitted on a different span at train and at serve time is a
# train/serve skew on the very quantity the model is trained to correct.
#
# 730 rather than 365, measured causally on 2026-08-04 (same evaluation window,
# only the fit depth changing): residual MAE 16.82 -> 11.87 cm at Brest (-29%),
# 17.44 -> 15.55 cm at Saint-Malo. 365 days is *exactly* the Rayleigh threshold
# for Sa, so at that depth the annual constituent sits on the edge of its own
# separability — estimated noisily, and extrapolated forward badly at every
# refit. Two years conditions it properly. See `docs/plan-dev-modele.md`.
FIT_LOOKBACK_DAYS = 730


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
    lookback_days: int = FIT_LOOKBACK_DAYS,
) -> pd.Series:
    """Harmonic baseline over `times`, refitted every `refit_days` on past obs only.

    Anti-leak contract, structural like `features.build_features`: the model
    serving a valid time `v` is fitted on observations *strictly before* a cutoff
    `c <= v - horizon_hours`. Since a forecast issued at `t0` only covers
    `v <= t0 + horizon_hours`, that cutoff is always `<= t0` — no observation
    posterior to the issue can reach the fit, whatever the caller passes in.

    Consequence: values before `first_cutoff + horizon_hours` cannot be served
    causally and are absent from the returned series.

    Each fit sees a *sliding* `lookback_days` window, never the expanding history:
    production can only ever fetch a bounded window (`daily.TIDE_FIT_LOOKBACK_DAYS`),
    so letting the backtest fit on more would train the model to correct a better
    baseline than the one it is served — the skew this whole module exists to avoid.
    """
    if refit_days < 1:
        raise ValueError(f"refit_days must be >= 1, got {refit_days}")
    if lookback_days < 1:
        raise ValueError(f"lookback_days must be >= 1, got {lookback_days}")
    # ponytail: refit every 30d rather than at every issue — the model serving t0 is
    # up to refit_days + horizon stale (~32 d). Refitting daily costs ~180 utide.solve
    # per station for a drift that is millimetric over a month; go per-issue only if
    # the residual bias ever proves to grow within a refit interval.
    times = pd.DatetimeIndex(times).sort_values()
    horizon = pd.Timedelta(hours=horizon_hours)
    step = pd.Timedelta(days=refit_days)
    lookback = pd.Timedelta(days=lookback_days)

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
        past = obs[(obs.index < cutoff) & (obs.index >= cutoff - lookback)].dropna()
        if len(chunk) == 0 or past.empty:
            continue
        parts.append(fit(past, lat).predict(chunk))
    return pd.concat(parts) if parts else pd.Series(dtype=float, index=times[:0])
