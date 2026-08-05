#!/usr/bin/env python
"""Combien du gain publié un simple ridge capture-t-il déjà ?

Run:  cd pipeline && uv run python scripts/compare_ridge.py [--station IDS]

Rejoue le protocole d'évaluation de `train.py` (mêmes origines rolling, même
purge 48 h, même IC95 bootstrap par jour d'émission) une fois par candidat
forcé, plus une fois en sélection automatique — celle qui tourne en production.
Aucun effet de bord : `train.evaluate` n'écrit rien, l'artefact n'est sérialisé
que dans un répertoire temporaire pour en mesurer la taille. `models/` et
`docs/model-eval.md` ne sont pas touchés.

Deux tableaux :

* le premier donne l'IC95 du gain de chaque candidat, comme `model-eval.md` ;
* le second donne l'IC95 de l'**écart** ridge − incumbent, bootstrap **apparié**
  sur les mêmes jours d'émission. C'est le seul des deux qui conclut : deux
  intervalles qui se chevauchent ne disent rien d'un écart mesuré sur les mêmes
  heures.

Le coût rapporté est celui du protocole entier (tous les folds + le refit
production) : c'est une comparaison relative entre candidats, pas un temps de
fit unitaire.
"""

from __future__ import annotations

import argparse
import tempfile
import time
from pathlib import Path

import numpy as np
import train
from scoreboard import model
from scoreboard.config import load_env, load_stations

CONFIGS = {
    "ridge": ("ridge",),
    "hgb": ("hgb",),
    "hgb-per-lead": ("hgb-per-lead",),
    "auto (production)": model.MODEL_NAMES,
}
DRAWS = 2_000


def run(station, names: tuple[str, ...], staging: Path) -> dict | None:
    started = time.perf_counter()
    row = train.evaluate(station, train._test_days(station.kind), model_names=names)
    if row is None:
        return None
    row["seconds"] = time.perf_counter() - started
    row["bytes"] = model.stage(row["_estimator"], station.id, staging).stat().st_size
    return row


def paired_gain_delta(incumbent: dict, ridge: dict) -> tuple[float, float, float]:
    """(Δ gain hors biais, IC95 bas, IC95 haut) de `incumbent` − `ridge`.

    Les deux candidats sont évalués sur exactement les mêmes lignes de test et
    contre la même baseline : le dénominateur débiaisé est commun, et l'écart se
    réduit à la différence des erreurs absolues. Le rééchantillonnage porte sur
    des jours d'émission entiers, comme l'IC de `train.py` — les 48 leads d'un
    même run ne sont pas des observations indépendantes.
    """
    level_incumbent, x_test, obs, fold_ids = incumbent["_test_eval"]
    level_ridge, x_ridge, obs_ridge, _ = ridge["_test_eval"]
    if not x_test.index.equals(x_ridge.index):
        raise ValueError("les deux candidats n'ont pas été évalués sur les mêmes lignes")
    residual = obs.to_numpy() - x_test["baseline"].to_numpy()
    err_incumbent = np.abs(level_incumbent - obs.to_numpy())
    err_ridge = np.abs(level_ridge - obs_ridge.to_numpy())
    days = train.issue_days(x_test)
    groups = [np.flatnonzero(days == day) for day in days.unique().sort_values()]

    def delta(indices: np.ndarray) -> float | None:
        base = train._debiased_baseline_error(residual[indices], fold_ids[indices]).sum()
        if not base:
            return None
        return (err_ridge[indices].sum() - err_incumbent[indices].sum()) / base

    point = delta(np.arange(len(err_ridge)))
    if len(groups) < 2 or point is None:
        return float("nan"), float("nan"), float("nan")
    rng = np.random.default_rng(20260805)
    values = [
        value
        for picks in rng.integers(0, len(groups), size=(DRAWS, len(groups)))
        if (value := delta(np.concatenate([groups[i] for i in picks]))) is not None
    ]
    if not values:
        return float(point), float("nan"), float("nan")
    low, high = np.quantile(values, (0.025, 0.975))
    return float(point), float(low), float(high)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--station", default="", metavar="IDS", help="ids séparés par des virgules")
    args = ap.parse_args()
    load_env()

    stations = load_stations()
    if only := {s.strip() for s in args.station.split(",") if s.strip()}:
        stations = [s for s in stations if s.id in only]

    per_candidate = [
        "| Station | Type | Candidat | MAE modèle | Gain hors biais | IC95 % gain | "
        "Protocole | Coût protocole (s) | Artefact |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    deltas = [
        "| Station | Type | Incumbent | Gain ridge | Gain incumbent | "
        "Δ (incumbent − ridge) | IC95 % de l'écart | Conclusion |",
        "|---|---|---|---|---|---|---|---|",
    ]
    with tempfile.TemporaryDirectory(prefix="compare-ridge-") as tmp:
        staging = Path(tmp)
        for station in stations:
            rows = {}
            for label, names in CONFIGS.items():
                row = run(station, names, staging)
                if row is None:
                    print(f"{station.id}: pas de dataset — ignorée")
                    break
                rows[label] = row
                suffix = f" (`{row['ml_model']}`)" if len(names) > 1 else ""
                per_candidate.append(
                    f"| {station.id} | {station.kind} | `{label}`{suffix} | "
                    f"{row['mae_model']:.4f} | {row['gain_debiased']:+.1%} | "
                    f"[{row['gain_debiased_ci95_low']:+.1%}, "
                    f"{row['gain_debiased_ci95_high']:+.1%}] | "
                    f"{row['evaluation_protocol']} ({row['n_folds']}×{row['test_days']}j) | "
                    f"{row['seconds']:.1f} | {row['bytes'] / 1024:.0f} ko |"
                )
            if "ridge" not in rows or "auto (production)" not in rows:
                continue
            ridge, auto = rows["ridge"], rows["auto (production)"]
            point, low, high = paired_gain_delta(auto, ridge)
            verdict = "boosting payé" if low > 0 else "boosting non payé"
            deltas.append(
                f"| {station.id} | {station.kind} | `{auto['ml_model']}` | "
                f"{ridge['gain_debiased']:+.1%} | {auto['gain_debiased']:+.1%} | "
                f"{point * 100:+.1f} pt | [{low * 100:+.1f}, {high * 100:+.1f}] pt | "
                f"**{verdict}** |"
            )

    print("\n".join(per_candidate))
    print()
    print("\n".join(deltas))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
