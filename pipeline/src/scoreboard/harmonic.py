"""Thin wrapper around utide.solve/reconstruct — astronomical tide baseline."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import joblib
import pandas as pd
import utide

from scoreboard.model import MODELS_DIR

# How much past observation one tidal analysis is allowed to see. One year, and
# the number is physical, not a tuning knob: two constituents are only separable
# over a record longer than the inverse of their frequency gap (Rayleigh). Below
# ~182 days utide cannot separate S2/K2 nor K1/P1 and *infers* them from fixed
# admittance ratios instead of solving them; below ~365 days the annual Sa (and
# with it the seasonal mean-sea-level swing, ~5-10 cm at Brest) is not in the
# basis at all and leaks into the fitted mean — a bias that then drifts with the
# season. This constant is the single source of truth for both the training
# backtest (`causal_predict`) and the persisted constants production serves
# (`scripts/fit_harmonic.py`): a baseline fitted on a different span at train and
# at serve time is a train/serve skew on the very quantity the model is trained
# to correct.
#
# 730 rather than 365, measured causally on 2026-08-04 (same evaluation window,
# only the fit depth changing): residual MAE 16.82 -> 11.87 cm at Brest (-29%),
# 17.44 -> 15.55 cm at Saint-Malo. 365 days is *exactly* the Rayleigh threshold
# for Sa, so at that depth the annual constituent sits on the edge of its own
# separability — estimated noisily, and extrapolated forward badly at every
# refit. Two years conditions it properly. See `docs/plan-dev-modele.md`.
FIT_LOOKBACK_DAYS = 730

# Cadence de ré-ajustement des constantes, partagée par la production
# (`daily._baseline_window`, qui sert un artefact persisté) et le backtest
# (`causal_predict`, qui rejoue la même fraîcheur). Elle doit rester une seule
# constante : une production qui rafraîchit tous les 6 mois pendant que le
# backtest rafraîchit tous les 30 jours note une baseline plus fraîche que celle
# servie — le skew train/serve, réintroduit sur la baseline elle-même.
#
# 180 plutôt que 30, mesuré le 2026-08-04 par `causal_predict` sur la même
# fenêtre d'évaluation (2025-07-12 -> 2026-08-04, 9313 h, `trend=False`) : MAE de
# la baseline 11,65 -> 11,72 cm à Brest et 14,99 -> 15,63 cm à Saint-Malo. Six
# mois de péremption coûtent donc 0,7 mm à Brest et 6,4 mm à Saint-Malo — sous le
# centimètre, pour 6× moins de fits et un fetch quotidien divisé par 500. La
# cadence annuelle, elle, se paie : 16,11 cm à Saint-Malo (+1,1 cm sur 30 j).
# 180 est le palier avant ce décrochage, pas un maximum arbitraire.
REFIT_DAYS = 180


@dataclass
class HarmonicModel:
    coef: object  # utide.Bunch, opaque solution container
    # Dernière observation vue par le fit. C'est elle qui date les constantes,
    # pas l'instant d'écriture : un artefact ajusté aujourd'hui sur des obs
    # arrêtées il y a six mois est vieux de six mois. `daily` refuse de servir
    # au-delà de `REFIT_DAYS` à partir de là — sans quoi un cron cassé sert
    # silencieusement un fit vieux de deux ans.
    fitted_at: pd.Timestamp | None = None

    def predict(self, times: pd.DatetimeIndex) -> pd.Series:
        recon = utide.reconstruct(times, self.coef, verbose=False)
        return pd.Series(recon.h, index=times)

    def save(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        joblib.dump({"coef": self.coef, "fitted_at": self.fitted_at}, path)

    @classmethod
    def load(cls, path: str | Path) -> "HarmonicModel":
        return cls(**joblib.load(path))


def artifact_path(station_id: str, models_dir: Path | None = None) -> Path:
    """Où vivent les constantes persistées d'une station. Suffixe distinct de
    `models/<id>.joblib` (le correcteur ML) : deux artefacts de nature et de
    cadence différentes, jamais le même fichier."""
    return (models_dir or MODELS_DIR) / f"{station_id}-harmonic.joblib"


def fit(obs: pd.Series, lat: float) -> HarmonicModel:
    # `trend=False` : utide extrapole sinon la tendance séculaire qu'il a
    # estimée, et un fit figé la propage linéairement hors de sa fenêtre. C'est
    # la cicatrice du module — un fit de 90 j gelé avait porté un offset de
    # -0,3 m. Sur 730 j la tendance est bien conditionnée, mais l'extrapoler
    # reste un risque sans contrepartie dès lors qu'on sert le fit pendant
    # `REFIT_DAYS`.
    coef = utide.solve(obs.index, obs.values, lat=lat, trend=False, verbose=False)
    return HarmonicModel(coef=coef, fitted_at=obs.index[-1])


def causal_predict(
    obs: pd.Series,
    lat: float,
    times: pd.DatetimeIndex,
    first_cutoff: pd.Timestamp,
    refit_days: int = REFIT_DAYS,
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
    production fits on exactly that bounded window (`scripts/fit_harmonic.py`), so
    letting the backtest fit on more would train the model to correct a better
    baseline than the one it is served — the skew this whole module exists to avoid.
    Same rule pour la fraîcheur : `refit_days` est la cadence réellement tenue en
    production, pas un pas de simulation libre.
    """
    if refit_days < 1:
        raise ValueError(f"refit_days must be >= 1, got {refit_days}")
    if lookback_days < 1:
        raise ValueError(f"lookback_days must be >= 1, got {lookback_days}")
    # `refit_days` par défaut = `REFIT_DAYS`, la cadence que la production tient
    # vraiment (elle sert un artefact persisté). Le passer plus court ici mesure
    # une baseline que personne ne sert.
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
