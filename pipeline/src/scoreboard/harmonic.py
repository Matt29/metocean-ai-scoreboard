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

# Couverture minimale de la fenêtre de fit, en fraction. Porte sur la
# *couverture* et pas sur un compte d'heures plein : REFMAR a des trous (de
# l'ordre du pour-cent sur deux ans, jamais mesuré finement), donc
# exiger 17520 h pleines sur une demande de 730 jours ne serait jamais satisfait.
# Ce qui compte est que la fenêtre *couvre* deux ans — en dessous, utide ne
# sépare ni S2/K2 ni Sa et rend des amplitudes fausses sans jamais lever.
#
# Partagé entre l'entraînement (`build_dataset.build_tide`) et la production
# (`daily.refit_harmonic`) : les deux posaient la même question avec deux seuils
# différents (100 % à l'entraînement, 90 % en production), donc une station
# pouvait être jugée ajustable d'un côté et pas de l'autre. C'est la famille de
# skew que ce module entier existe pour fermer.
FIT_COVERAGE_FLOOR = 0.9


def enough_for_fit(level: pd.Series) -> bool:
    """Assez d'observations pour que `fit` rende des constantes crédibles."""
    return len(level) >= int(FIT_COVERAGE_FLOOR * 24 * FIT_LOOKBACK_DAYS)

# Cadence de ré-ajustement des constantes, partagée par la production
# (`daily._baseline_window`, qui sert un artefact persisté) et le backtest
# (`causal_predict`, qui rejoue la même fraîcheur). Elle doit rester une seule
# constante : une production qui rafraîchit tous les 6 mois pendant que le
# backtest rafraîchit tous les 30 jours note une baseline plus fraîche que celle
# servie — le skew train/serve, réintroduit sur la baseline elle-même.
#
# 30 plutôt que 180, et l'histoire de ce choix vaut d'être gardée parce qu'elle
# a failli partir dans l'autre sens. Mesuré d'abord sur la **baseline seule**
# (`causal_predict`, 2025-07-12 -> 2026-08-04, 9313 h, `trend=False`) : 11,65 ->
# 11,72 cm à Brest et 14,99 -> 15,63 cm à Saint-Malo de 30 j à 180 j. Six mois de
# péremption ne coûtaient donc que 0,7 mm à Brest — verdict : 180.
#
# Re-mesuré **de bout en bout, après le modèle ML**, la seule quantité qu'on
# publie : brest +50,7 % -> +48,7 % de gain hors biais, MAE 5,8 -> 6,1 cm. Le
# produit perd 3 mm là où la baseline seule n'en perdait que 0,7 : le modèle
# compense en partie les refits fréquents, et une mesure sur la baseline seule ne
# pouvait pas le voir. Saint-Malo est neutre (10,6 -> 10,8 cm).
#
# 30 j rend donc 2 points à Brest et ne coûte rien d'opérationnel : le fit de
# ~50 s tombe une fois par mois au lieu d'une fois par jour, et le semestre
# n'aurait économisé que 50 s de plus par an. Leçon générale du dépôt, payée une
# fois de plus : mesurer la quantité publiée, jamais un proxy en amont.
REFIT_DAYS = 30


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
