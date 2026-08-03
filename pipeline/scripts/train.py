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
import json
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
GATE_PATH = model.MODELS_DIR / "gate.json"
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
    row = {
        "station": station_id,
        "kind": kind,
        "n_train": int((~is_test).sum()),
        "n_test": int(is_test.sum()),
        "mae_base": mae_base,
        "mae_model": mae_model,
        "bias": bias,
        "mae_debiased": mae_debiased,
        "gain": gain,
        # The honest headline: gain over the baseline once its constant offset is gone.
        "gain_debiased": (mae_debiased - mae_model) / mae_debiased if mae_debiased else 0.0,
        "pass": gain >= GATE,
        # "weak": the model brings nothing a constant offset would not.
        "weak": mae_model >= mae_debiased,
    }
    print(
        f"  {station_id}: train {(~is_test).sum()} / test {is_test.sum()} rows | "
        f"MAE base {mae_base:.3f} -> model {mae_model:.3f} ({gain:+.1%}) | "
        f"{_verdict(row)} -> {saved.name}"
    )
    return row


def _verdict(r: dict) -> str:
    """`PASS*` = au-dessus du gate mais sans battre un simple débiaisage."""
    if not r["pass"]:
        return "FAIL"
    return "PASS*" if r["weak"] else "PASS"


def _failure_notes(rows: list[dict]) -> list[str]:
    """Réserve 4, entièrement dérivée des chiffres — aucune station codée en dur."""
    failing = [r for r in rows if not r["pass"]]
    if not failing:
        return ["4. **Aucune station sous le gate sur cette fenêtre de test.**", ""]
    notes = [
        "4. **Stations sous le gate — à ne pas publier en l'état.** Le modèle n'y",
        f"   atteint pas les +{GATE:.0%} exigés : il ne trouve pas de signal exploitable",
        "   dans les features actuelles. Deux causes à trancher station par station —",
        "   historique d'entraînement trop court, ou absence d'une feature de forçage",
        "   (vent ARPEGE) sans laquelle le résidu restant est imprévisible.",
        "",
    ]
    notes += [
        f"   * `{r['station']}` ({r['kind']}) : {r['n_train']} lignes de train, MAE "
        f"baseline {r['mae_base']:.3f} → modèle {r['mae_model']:.3f} "
        f"({r['gain']:+.1%} affiché, {r['gain_debiased']:+.1%} hors biais)"
        for r in failing
    ]
    return notes + [""]


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
        " MAE modèle | Gain affiché | **Gain hors biais** | Verdict |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        lines.append(
            f"| {r['station']} | {r['kind']} | {r['n_train']} / {r['n_test']} | "
            f"{r['mae_base']:.3f} | {r['mae_debiased']:.3f} | {r['mae_model']:.3f} | "
            f"{r['gain']:+.1%} | **{r['gain_debiased']:+.1%}** | "
            f"{_verdict(r).replace('*', r'\*')} |"
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
        "**`PASS*`** = la station passe le gate mais **ne bat pas sa propre baseline",
        "débiaisée** : son gain affiché est essentiellement une constante, pas du skill.",
        "Ne pas mettre ce chiffre en avant sans la réserve 3.",
        "",
        "Ce verdict est aussi émis en donnée dans `pipeline/models/gate.json`",
        "(`{station: {pass, weak, mae_model, mae_baseline, gain, gain_debiased}}`) —",
        "c'est cette",
        "source, pas ce tableau, que le publisher doit lire.",
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
    ]
    # Tout est dérivé : la fermeté de la phrase suit les chiffres, elle ne les précède pas.
    inflated = [r for r in rows if r["gain"] > 0 and r["gain_debiased"] < 0.5 * r["gain"]]
    weak = [r["station"] for r in rows if r["weak"]]
    lines += [
        f"3. **Sur {len(inflated)} des {len(rows)} stations, plus de la moitié du gain",
        "   affiché n'est qu'une correction de biais constant** — chaque baseline dérive",
        "   sur la fenêtre de test, et retirer ce seul offset capte déjà l'essentiel du",
        "   gain. Le chiffre à citer est donc **« Gain hors biais »**, jamais « Gain",
        "   affiché ». Détail par station (biais obs − baseline, puis les deux gains) :",
        "",
    ]
    lines += [
        f"   * `{r['station']}` : biais {r['bias']:+.3f} m — "
        f"gain affiché {r['gain']:+.1%}, **hors biais {r['gain_debiased']:+.1%}**"
        for r in rows
    ]
    lines += [
        "",
        (
            "   Stations dont le gain affiché vaut **au moins le double** de son gain "
            f"hors biais : {', '.join(f'`{r['station']}`' for r in inflated)} — leur "
            "chiffre de tête est d'abord du débiaisage."
            if inflated
            else "   Aucune station n'a un gain affiché supérieur au double de son gain "
            "hors biais."
        ),
        (
            f"   Stations où le modèle **ne bat pas** ce simple débiaisage : "
            f"{', '.join(f'`{s}`' for s in weak)} — il n'y apporte rien de plus qu'une"
            " constante, à ne pas présenter comme du skill météo-océanique."
            if weak
            else "   Toutes les stations battent ce simple débiaisage."
        ),
    ]
    lines += _failure_notes(rows)
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

    # Machine-readable gate: the publisher (Task 8) reads this, not the markdown.
    gate = {
        r["station"]: {
            "pass": r["pass"],
            "weak": r["weak"],
            "mae_model": round(r["mae_model"], 4),
            "mae_baseline": round(r["mae_base"], 4),
            "gain": round(r["gain"], 4),
            "gain_debiased": round(r["gain_debiased"], 4),
        }
        for r in rows
    }
    GATE_PATH.write_text(json.dumps(gate, indent=2, sort_keys=True) + "\n")

    write_report(rows, args.test_days)
    failed = [r["station"] for r in rows if not r["pass"]]

    print(f"\nreport -> {REPORT_PATH}")
    print(f"gate: {len(rows) - len(failed)}/{len(rows)} PASS" + (f", FAIL: {failed}" if failed else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
