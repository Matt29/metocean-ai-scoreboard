#!/usr/bin/env python
"""Train one post-processing model per station and write the eval report.

Run:  cd pipeline && uv run python scripts/train.py [--test-days 30]

Split
-----
Temporal, **by issue day** — never random. A dataset row is one (issue, lead)
pair, so two rows of the same 06 UTC issue share `last_err` / `mean_err_24h`;
splitting on valid time would leak an issue across train and test. The issue day
is recovered as `valid_time - lead_h`. The last `--test-days` issue days are the
test set.

Tide stations
-------------
The learned target is the residual `obs - harmonic`; MAE is nevertheless
reported on the reassembled water level (`harmonic + residual`) so the numbers
are comparable with the wave stations.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from scoreboard import model
from scoreboard.config import load_stations
from scoreboard.features import FEATURE_COLUMNS

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "pipeline" / "data_train"
REPORT_PATH = ROOT / "docs" / "model-eval.md"
GATE = 0.05  # the model must beat the baseline by >= 5% to go live
UNIT = {"wave": "m (Hs)", "tide": "m (water level)"}


def split_by_issue_day(x: pd.DataFrame, test_days: int) -> np.ndarray:
    """Boolean mask of the test rows: the last `test_days` issue days."""
    issue_day = pd.DatetimeIndex(x.index - pd.to_timedelta(x["lead_h"], unit="h")).normalize()
    cutoff = issue_day.max() - pd.Timedelta(days=test_days)
    return np.asarray(issue_day > cutoff)


def evaluate(station_id: str, kind: str, test_days: int) -> dict | None:
    path = DATA_DIR / f"{station_id}.parquet"
    if not path.exists():
        print(f"  {station_id}: no dataset at {path} — skipped")
        return None

    df = pd.read_parquet(path)
    x, obs = df[FEATURE_COLUMNS], df["y"].astype(float)
    # Tide: learn the residual; waves: learn the corrected value directly.
    target = obs - x["baseline"] if kind == "tide" else obs

    is_test = split_by_issue_day(x, test_days)
    if is_test.all() or not is_test.any():
        print(f"  {station_id}: not enough history for a {test_days}d test split — skipped")
        return None

    m = model.train(x[~is_test], target[~is_test])
    x_test, obs_test = x[is_test], obs[is_test]
    pred = model.predict(m, x_test)
    level = x_test["baseline"].to_numpy() + pred if kind == "tide" else pred

    mae_model = float(np.abs(level - obs_test.to_numpy()).mean())
    resid = obs_test.to_numpy() - x_test["baseline"].to_numpy()
    mae_base = float(np.abs(resid).mean())
    gain = (mae_base - mae_model) / mae_base if mae_base else 0.0
    # How much of the baseline error is a plain constant offset (see report §3).
    bias = float(resid.mean())
    mae_debiased = float(np.abs(resid - bias).mean())

    saved = model.save(m, station_id)
    print(
        f"  {station_id}: train {(~is_test).sum()} / test {is_test.sum()} rows | "
        f"MAE base {mae_base:.3f} -> model {mae_model:.3f} ({gain:+.1%}) | "
        f"{'PASS' if gain >= GATE else 'FAIL'} -> {saved.name}"
    )
    return {
        "station": station_id,
        "kind": kind,
        "n_train": int((~is_test).sum()),
        "n_test": int(is_test.sum()),
        "mae_base": mae_base,
        "mae_model": mae_model,
        "bias": bias,
        "mae_debiased": mae_debiased,
        "gain": gain,
        "pass": gain >= GATE,
    }


def write_report(rows: list[dict], test_days: int) -> None:
    lines = [
        "# Évaluation des modèles de post-traitement",
        "",
        f"Généré par `pipeline/scripts/train.py` le "
        f"{datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC "
        f"(test = les {test_days} derniers jours d'émission).",
        "",
        "Le modèle **post-traite** la prévision physique officielle (MFWAM pour les",
        "vagues, harmonique pour le niveau d'eau) : il la corrige, il ne la remplace",
        "jamais.",
        "",
        "## Résultats par station",
        "",
        "| Station | Type | Rows train / test | MAE baseline | MAE baseline débiaisée |"
        " MAE modèle | Gain | Verdict |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        lines.append(
            f"| {r['station']} | {r['kind']} | {r['n_train']} / {r['n_test']} | "
            f"{r['mae_base']:.3f} | {r['mae_debiased']:.3f} | {r['mae_model']:.3f} | "
            f"{r['gain']:+.1%} | {'PASS' if r['pass'] else 'FAIL'} |"
        )
    lines += [
        "",
        f"MAE en {UNIT['wave']} pour les stations `wave`, en {UNIT['tide']} pour les",
        "stations `tide`. « MAE baseline débiaisée » = MAE de la baseline après retrait",
        "de son seul biais moyen sur la fenêtre de test — c'est le garde-fou de la",
        "réserve 3 : un modèle qui ne bat pas cette colonne n'apporte rien de plus",
        "qu'une constante. Gate de mise en ligne : **+5 % de MAE gagnée** sur la",
        "baseline. Une station FAIL reste entraînée et son artefact reste versionné,",
        "mais elle ne doit pas être publiée telle quelle sur le scoreboard.",
        "",
    ]
    failed = [r["station"] for r in rows if not r["pass"]]
    lines += [
        f"**Stations sous le gate : {', '.join(failed)}** — à ne pas mettre en ligne en"
        " l'état.\n"
        if failed
        else "**Toutes les stations passent le gate.**\n",
        "## Protocole",
        "",
        "* **Split temporel par jour d'émission.** Une ligne du dataset est un couple",
        "  (émission 06 UTC, lead 1–48 h) ; les lignes d'une même émission partagent",
        "  `last_err` / `mean_err_24h`. Découper sur le temps de validité ferait donc",
        "  fuir une émission entre train et test. Le jour d'émission est reconstruit",
        f"  comme `valid_time - lead_h`, et les {test_days} derniers jours d'émission",
        "  forment le test. Jamais de split aléatoire.",
        "* **Cible.** Stations `wave` : l'observation Hs. Stations `tide` : le résidu",
        "  `obs - harmonique` ; le niveau publié est réassemblé en",
        "  `harmonique + résidu prédit`, et c'est sur ce niveau reconstitué que la MAE",
        "  ci-dessus est calculée — sinon les chiffres ne seraient pas comparables",
        "  entre stations.",
        "* Tous les horodatages sont en UTC.",
        "",
        "## Réserves importantes sur l'interprétation",
        "",
        "1. **Le skill des stations `wave` est un plafond mesuré sur analyse, pas sur",
        "   prévision réelle.** Faute d'archive libre des runs MFWAM passés, la",
        "   baseline d'entraînement est l'**analyse** MFWAM, qui assimile les bouées",
        "   Candhis — donc les observations mêmes qui servent de vérité terrain. Le",
        "   couple (baseline, obs) vu à l'entraînement n'est donc pas celui que verra",
        "   la production : ces gains sont un **plafond mesuré sur analyse**, pas une",
        "   estimation du skill opérationnel, et la direction de l'écart n'est pas",
        "   déterminable a priori. Le ré-entraînement sur de vraies prévisions",
        "   archivées interviendra après ~1 mois de runs quotidiens ; ces chiffres",
        "   seront alors remplacés.",
        "2. **Le gate de +5 % s'applique quand même**, mais il se lit",
        "   « +5 % mesuré sur analyse », pas « +5 % en opérationnel ».",
        "3. **Une large part du gain, sur TOUTES les stations, n'est qu'une correction",
        "   de biais constant.** Chaque baseline dérive sur la fenêtre de test (pour",
        "   les stations `tide`, parce que l'harmonique a été fittée sur les 50 % les",
        "   plus anciens de l'historique ; pour les stations `wave`, biais MFWAM sur la",
        "   période). Biais mesuré (obs − baseline) par station :",
        "",
    ]
    for r in rows:
        lines.append(f"   * `{r['station']}` : biais {r['bias']:+.3f} m")
    weak = [r["station"] for r in rows if r["mae_model"] >= r["mae_debiased"]]
    lines += [
        "",
        "   La colonne « MAE baseline débiaisée » du tableau isole ce qui reste une",
        "   fois cette constante retirée : c'est elle, et non la MAE baseline brute,",
        "   qui mesure le vrai apport du modèle.",
        (
            f"   Stations où le modèle **ne bat pas** ce simple débiaisage : "
            f"{', '.join(f'`{s}`' for s in weak)} — leur gain affiché est essentiellement"
            " une constante, à ne pas présenter comme du skill météo-océanique."
            if weak
            else "   Toutes les stations battent ce simple débiaisage."
        ),
        "4. **`anglet` a un historique court** (obs Candhis à partir du 2025-11-18,",
        "   panne de bouée avant) : ~30 % de train en moins que les autres stations",
        "   vagues, et un test plus bruité. C'est la station qui échoue au gate.",
        "   Pistes avant de la publier : plus d'historique, ou une feature de vent",
        "   ARPEGE. Décision de re-spécification à prendre hors Task 7.",
        "",
    ]
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text("\n".join(lines))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--test-days", type=int, default=30, help="issue days held out for test")
    args = ap.parse_args()

    print(f"Training (test = last {args.test_days} issue days):")
    rows = [
        r
        for st in load_stations()
        if (r := evaluate(st.id, st.kind, args.test_days)) is not None
    ]
    if not rows:
        print("nothing trained")
        return 1

    write_report(rows, args.test_days)
    failed = [r["station"] for r in rows if not r["pass"]]

    print(f"\nreport -> {REPORT_PATH}")
    print(f"gate: {len(rows) - len(failed)}/{len(rows)} PASS" + (f", FAIL: {failed}" if failed else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
