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

Wave baseline
-------------
The physical baseline is no longer a single hard-coded model: each wave station
picks, among the 5 Open-Meteo wave models of its raw parquet, the one closest to
its own buoy — **on the train issue days only** (`select_baseline`). Picking it
on the whole window would let the test set choose the yardstick it is then
measured against.

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
from scoreboard.config import Station, load_env, load_stations
from scoreboard.dataset import assemble
from scoreboard.features import FEATURE_COLUMNS, WAVE_FEATURE_COLUMNS
from scoreboard.sources.marine import MODEL_COLUMNS
from scoreboard.sources.wind import MULTI_FORCING_COLUMNS

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "pipeline" / "data_train"
REPORT_PATH = ROOT / "docs" / "model-eval.md"
GATE_PATH = model.MODELS_DIR / "gate.json"
GATE = 0.05  # the model must beat the baseline by >= 5% to go live
UNIT = {"wave": "m (Hs)", "tide": "m (water level)"}
ABLATABLE = sorted(set(FEATURE_COLUMNS) | set(WAVE_FEATURE_COLUMNS))


def issue_days(x: pd.DataFrame) -> pd.DatetimeIndex:
    """Issue day of each row, recovered as `valid_time - lead_h`."""
    return pd.DatetimeIndex(x.index - pd.to_timedelta(x["lead_h"], unit="h")).normalize()


def split_by_issue_day(
    x: pd.DataFrame, test_days: int, cutoff: pd.Timestamp | None = None
) -> np.ndarray:
    """Boolean mask of the test rows: the last `test_days` issue days."""
    day = issue_days(x)
    cutoff = day.max() - pd.Timedelta(days=test_days) if cutoff is None else cutoff
    return np.asarray(day > cutoff)


def select_baseline(raw: pd.DataFrame, train_days: pd.DatetimeIndex) -> str:
    """`hs_<model>` column of lowest MAE vs `raw["hs"]` over `train_days` only.

    Restricting to the train days is the whole point: the selected model is the
    yardstick every gain below is measured against, so it must be chosen without
    ever looking at the test window.
    """
    day = pd.DatetimeIndex(raw.index).normalize()
    sub = raw[day.isin(train_days)]
    mae = {
        col: float((sub[col] - sub["hs"]).abs().mean())
        for col in MODEL_COLUMNS
        if col in sub.columns
    }
    mae = {c: v for c, v in mae.items() if np.isfinite(v)}
    if not mae:
        raise ValueError("no wave model column overlaps the observations on the train days")
    return min(mae, key=mae.__getitem__)


def _wave_data(station: Station, test_days: int) -> tuple | None:
    """(x, obs, is_test, baseline_model) assembled from the raw multi-model parquet."""
    path = DATA_DIR / f"{station.id}_raw.parquet"
    if not path.exists():
        print(f"  {station.id}: no raw dataset at {path} — skipped")
        return None

    raw = pd.read_parquet(path).sort_index()
    # Days are counted on those that carry an observation: a station whose buoy
    # stopped early must still get its 30 *usable* test days.
    obs_days = pd.DatetimeIndex(raw.index[raw["hs"].notna()]).normalize().unique()
    cutoff = obs_days.max() - pd.Timedelta(days=test_days)
    baseline_col = select_baseline(raw, obs_days[obs_days <= cutoff])

    x, obs = assemble(
        station,
        raw[["hs"]],
        raw[[baseline_col]],
        raw[MULTI_FORCING_COLUMNS],
        wave_models=raw[MODEL_COLUMNS],
    )
    if x.empty:
        print(f"  {station.id}: assembled 0 row — skipped")
        return None
    # Same `cutoff` timestamp for the split as for the selection, and a row's
    # issue day is never after its valid day (lead >= 0): every row that fed the
    # baseline choice (valid day <= cutoff) is therefore a train row. No leak.
    return (
        x,
        obs.astype(float),
        split_by_issue_day(x, test_days, cutoff),
        baseline_col.removeprefix("hs_"),
    )


def _tide_data(station: Station, test_days: int) -> tuple | None:
    """(x, obs, is_test, None) from the pre-assembled tide dataset."""
    path = DATA_DIR / f"{station.id}.parquet"
    if not path.exists():
        print(f"  {station.id}: no dataset at {path} — skipped")
        return None
    df = pd.read_parquet(path)
    x = df[FEATURE_COLUMNS].copy()
    return x, df["y"].astype(float), split_by_issue_day(x, test_days), None


def evaluate(
    station: Station,
    test_days: int,
    ablate: tuple[str, ...] = (),
    model_names: tuple[str, ...] = model.MODEL_NAMES,
) -> dict | None:
    """Train every candidate on the same split; publish the best gain hors biais."""
    loaded = _wave_data(station, test_days) if station.kind == "wave" else _tide_data(
        station, test_days
    )
    if loaded is None:
        return None
    x, obs, is_test, baseline_model = loaded
    if is_test.all() or not is_test.any():
        print(f"  {station.id}: not enough history for a {test_days}d test split — skipped")
        return None

    if ablate:
        # Zeroing beats dropping: same rows, same split, same seed, same model
        # capacity — and for a tree ensemble a constant column is never split on,
        # so it is equivalent to removing the feature. This is what produced the
        # "with / without" ablation tables of the Task 7B / 7C reports.
        x = x.copy()
        x[[c for c in ablate if c in x.columns]] = 0.0
    # Tide: learn the residual; waves: learn the corrected value directly.
    target = obs - x["baseline"] if station.kind == "tide" else obs

    x_train, target_train = x[~is_test], target[~is_test]
    x_test, obs_test = x[is_test], obs[is_test]
    resid = obs_test.to_numpy() - x_test["baseline"].to_numpy()
    mae_base = float(np.abs(resid).mean())
    # How much of the baseline error is a plain constant offset (see report §4).
    bias = float(resid.mean())
    mae_debiased = float(np.abs(resid - bias).mean())

    fitted, scores = {}, {}
    for name in model_names:
        m = model.train(x_train, target_train, name=name)
        pred = model.predict(m, x_test)
        level = x_test["baseline"].to_numpy() + pred if station.kind == "tide" else pred
        mae_model = float(np.abs(level - obs_test.to_numpy()).mean())
        fitted[name] = m
        scores[name] = {
            "mae_model": mae_model,
            "gain": (mae_base - mae_model) / mae_base if mae_base else 0.0,
            # The honest headline: gain over the baseline once its offset is gone.
            "gain_debiased": (mae_debiased - mae_model) / mae_debiased if mae_debiased else 0.0,
        }

    # The judge is the gain hors biais (equivalently, the lowest MAE: the two
    # reference MAEs are the same for every candidate of a given station).
    best = max(scores, key=lambda n: scores[n]["gain_debiased"])
    row = {
        "station": station.id,
        "kind": station.kind,
        "baseline_model": baseline_model,
        "n_train": int((~is_test).sum()),
        "n_test": int(is_test.sum()),
        "mae_base": mae_base,
        "bias": bias,
        "mae_debiased": mae_debiased,
        "ml_model": best,
        "scores": scores,
        **scores[best],
        "pass": scores[best]["gain"] >= GATE,
        # "weak": the model brings nothing a constant offset would not.
        "weak": scores[best]["mae_model"] >= mae_debiased,
    }
    saved = (
        None
        if ablate  # ablation must not clobber the published artefact
        else model.save(fitted[best], station.id, baseline_model=baseline_model)
    )
    print(
        f"  {station.id}: train {row['n_train']} / test {row['n_test']} rows | "
        f"baseline {baseline_model or station.baseline} | "
        f"MAE base {mae_base:.3f} -> {best} {row['mae_model']:.3f} ({row['gain']:+.1%}) | "
        f"{_verdict(row)} -> {saved.name if saved else 'not saved (ablation)'}"
    )
    for name, s in scores.items():
        print(f"      {name:14} MAE {s['mae_model']:.4f}  hors biais {s['gain_debiased']:+7.1%}")
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
        return ["5. **Aucune station sous le gate sur cette fenêtre de test.**", ""]
    notes = [
        "5. **Stations sous le gate — à ne pas publier en l'état.** Le modèle n'y",
        f"   atteint pas les +{GATE:.0%} exigés : il ne trouve pas de signal exploitable",
        "   dans les features actuelles. Le forçage vent 10 m (`wind_u10`/`wind_v10`)",
        "   en fait partie depuis Task 7B — il a payé sur les stations de houle exposée",
        "   mais **pas** sur celles ci-dessous — et la pression au niveau de la mer, le",
        "   candidat suivant le plus évident, a été testée et écartée (voir la section",
        "   « Pistes testées et écartées »). L'explication est donc ailleurs :",
        "   historique d'entraînement trop court, forçage local mal représenté par la",
        "   maille du modèle atmosphérique, ou grandeur encore absente. À trancher",
        "   station par station, mesure à l'appui — `train.py --ablate <colonnes>` chiffre",
        "   ce que chaque feature apporte réellement (p. ex.",
        "   `--ablate wind_u10,wind_v10`).",
        "",
    ]
    notes += [
        f"   * `{r['station']}` ({r['kind']}) : {r['n_train']} lignes de train, MAE "
        f"baseline {r['mae_base']:.3f} → modèle {r['mae_model']:.3f} "
        f"({r['gain']:+.1%} affiché, {r['gain_debiased']:+.1%} hors biais)"
        for r in failing
    ]
    return notes + [""]


def _rejected_leads() -> list[str]:
    """Résultat négatif figé (Task 7C) — écrit pour qu'on ne le re-tente pas à l'aveugle.

    Chiffres non recalculés à chaque run : ils décrivent une expérience datée, sur
    une fenêtre datée. Le code de mesure, lui, est toujours là (`--ablate`).
    """
    return [
        "",
        "## Pistes testées et écartées",
        "",
        "* **Pression au niveau de la mer** (`pressure_msl` Open-Meteo, servie dans la",
        "  même requête que le vent, ajoutée comme anomalie à 1013,25 hPa). Motivation :",
        "  le baromètre inverse (~1 cm de niveau par hPa) est le premier moteur de la",
        "  surcote, donc du résidu à prédire sur les stations `tide`. **Mesurée le",
        "  2026-08-03 par ablation à fenêtre identique, elle dégrade 5 stations sur 6 et",
        "  a été retirée.** Δ de gain hors biais dus à la seule pression :",
        "",
        "  | station | kind | Δ pression |",
        "  |---|---|---|",
        "  | pierres-noires | wave | −2,0 pts |",
        "  | belle-ile | wave | −1,0 pt |",
        "  | anglet | wave | −2,4 pts |",
        "  | cherbourg | wave | −5,1 pts |",
        "  | brest | tide | −2,0 pts |",
        "  | saint-malo | tide | **+4,8 pts** (mais reste sous le gate) |",
        "",
        "  Seule `saint-malo` en profite, sans repasser au-dessus de son propre",
        "  débiaisage ; `anglet` tombait sous le gate à cause d'elle. Lecture la plus",
        "  simple : sur un historique court, une colonne sans effet direct sur les",
        "  stations `wave` ajoute surtout de la variance. Conditionner la feature au",
        "  `kind` de la station a été écarté : cela créerait deux chemins de",
        "  construction de features, alors que l'unicité de ce chemin est la garantie",
        "  centrale du projet contre le train/serve skew.",
        "  Détail : `.superpowers/sdd/2026-07-30-scoreboard-metocean-ia/task-7C-report.md`.",
        "",
    ]


def _gain_cell(row: dict, name: str) -> str:
    """Gain hors biais of one candidate, bold when it is the published one."""
    if name not in row["scores"]:
        return "—"  # candidate not run for this station (e.g. `--model`)
    gain = f"{row['scores'][name]['gain_debiased']:+.1%}"
    return f"**{gain}**" if name == row["ml_model"] else gain


def _ml_comparison(rows: list[dict]) -> list[str]:
    """Table station × candidate ML model, on the gain hors biais."""
    names = list(dict.fromkeys(n for r in rows for n in r["scores"]))
    lines = [
        "## Comparaison des modèles ML",
        "",
        "Gain **hors biais** de chaque candidat, entraîné et évalué sur exactement le",
        "même split et la même baseline physique que les autres. Le modèle publié par",
        "station est celui de meilleur gain hors biais (en gras). `ridge` est le",
        "**plancher honnête** : un gradient boosting qui ne le bat pas ne paie pas sa",
        "complexité, et c'est un résultat, pas un échec.",
        "",
        "| Station | Baseline physique | " + " | ".join(f"`{n}`" for n in names) + " | Publié |",
        "|---|---|" + "---|" * (len(names) + 1),
    ]
    for r in rows:
        cells = [_gain_cell(r, n) for n in names]
        lines.append(
            f"| {r['station']} | {r['baseline_model'] or r['kind']} | "
            + " | ".join(cells)
            + f" | `{r['ml_model']}` |"
        )
    return lines + [""]


def write_report(rows: list[dict], test_days: int, skipped: list[str] | None = None) -> None:
    lines = [
        "# Évaluation des modèles de post-traitement",
        "",
        f"Généré par `pipeline/scripts/train.py` le "
        f"{datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC "
        f"(test = les {test_days} derniers jours d'émission).",
        "",
        "Le modèle **post-traite** une prévision physique officielle : il la corrige, il",
        "ne la remplace jamais. Cette baseline n'est plus imposée : pour une station",
        "`wave`, c'est le **meilleur modèle physique** parmi les 5 modèles de vagues",
        "Open-Meteo, choisi station par station comme le plus proche de sa bouée **sur",
        "les seuls jours d'émission d'entraînement** (colonne « Baseline »). Pour une",
        "station `tide`, c'est la prédiction harmonique.",
        "",
        "## Résultats par station",
        "",
        "| Station | Type | Baseline (meilleur modèle physique) | Modèle ML |"
        " Rows train / test | MAE baseline | MAE baseline débiaisée |"
        " MAE modèle | Gain affiché | **Gain hors biais** | Verdict |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        lines.append(
            f"| {r['station']} | {r['kind']} | {r['baseline_model'] or 'harmonique'} | "
            f"`{r['ml_model']}` | {r['n_train']} / {r['n_test']} | "
            f"{r['mae_base']:.3f} | {r['mae_debiased']:.3f} | {r['mae_model']:.3f} | "
            f"{r['gain']:+.1%} | **{r['gain_debiased']:+.1%}** | "
            f"{_verdict(r).replace('*', r'\*')} |"
        )
    if skipped:
        lines += [
            "",
            f"**Stations non ré-entraînées sur cette fenêtre : {', '.join(skipped)}** — leur",
            "jeu d'entraînement est absent de `pipeline/data_train/`. Leur artefact et leur",
            "entrée `gate.json` du run précédent sont **conservés tels quels** : ils ne sont",
            "ni supprimés ni rafraîchis, et les chiffres ci-dessus ne les couvrent pas.",
        ]
    lines += [
        "",
        f"MAE en {UNIT['wave']} pour les stations `wave`, en {UNIT['tide']} pour les",
        "stations `tide`. « MAE baseline débiaisée » = MAE de la baseline après retrait",
        "de son seul biais moyen sur la fenêtre de test — c'est le garde-fou de la",
        "réserve 4 : un modèle qui ne bat pas cette colonne n'apporte rien de plus",
        "qu'une constante. Gate de mise en ligne : **+5 % de MAE gagnée** sur la",
        "baseline. Une station FAIL reste entraînée et son artefact reste versionné,",
        "mais elle ne doit pas être publiée telle quelle sur le scoreboard.",
        "",
        "**`PASS*`** = la station passe le gate mais **ne bat pas sa propre baseline",
        "débiaisée** : son gain affiché est essentiellement une constante, pas du skill.",
        "Ne pas mettre ce chiffre en avant sans la réserve 4.",
        "",
        "Ce verdict est aussi émis en donnée dans `pipeline/models/gate.json`",
        "(`{station: {pass, weak, mae_model, mae_baseline, gain, gain_debiased,",
        "baseline_model}}`) — c'est cette",
        "source, pas ce tableau, que le publisher doit lire.",
        "",
    ]
    failed = [r["station"] for r in rows if not r["pass"]]
    lines += [
        f"**Stations sous le gate : {', '.join(failed)}** — à ne pas mettre en ligne en"
        " l'état.\n"
        if failed
        else "**Toutes les stations passent le gate.**\n",
    ]
    lines += _ml_comparison(rows)
    lines += [
        "## Protocole",
        "",
        "* **Split temporel par jour d'émission.** Une ligne du dataset est un couple",
        "  (émission 06 UTC, lead 1–48 h) ; les lignes d'une même émission partagent",
        "  `last_err` / `mean_err_24h`. Découper sur le temps de validité ferait donc",
        "  fuir une émission entre train et test. Le jour d'émission est reconstruit",
        f"  comme `valid_time - lead_h`, et les {test_days} derniers jours d'émission",
        "  forment le test. Jamais de split aléatoire.",
        "* **Choix de la baseline (stations `wave`).** Les 5 modèles de vagues",
        "  Open-Meteo sont comparés à la bouée **sur les seuls jours d'émission",
        "  d'entraînement**, et le plus proche devient la baseline de la station — donc",
        "  le dénominateur de tous les gains ci-dessus. La sélection ne voit jamais la",
        "  fenêtre de test : sinon la baseline serait choisie par les données mêmes qui",
        "  servent à la juger, ce qui gonflerait mécaniquement le gain.",
        "* **Comparaison des modèles ML.** Les trois candidats (`hgb`, `ridge`,",
        "  `hgb-per-lead`) sont entraînés sur le même split, avec les mêmes features et",
        "  la même baseline ; celui de meilleur gain hors biais est publié.",
        "* **Cible.** Stations `wave` : l'observation Hs. Stations `tide` : le résidu",
        "  `obs - harmonique` ; le niveau publié est réassemblé en",
        "  `harmonique + résidu prédit`, et c'est sur ce niveau reconstitué que la MAE",
        "  ci-dessus est calculée — sinon les chiffres ne seraient pas comparables",
        "  entre stations.",
        "* Tous les horodatages sont en UTC.",
        "",
        "## Réserves importantes sur l'interprétation",
        "",
        "1. **Le skill des stations `wave` est un plafond mesuré sur passé reconstitué,",
        "   pas sur prévision réelle.** Faute d'archive libre des runs de vagues passés,",
        "   la baseline d'entraînement vient de la fenêtre historique de l'API Open-Meteo",
        "   Marine, qui n'est pas le run à +1–48 h qu'aura la production. Le couple",
        "   (baseline, obs) vu à l'entraînement n'est donc pas celui que verra la",
        "   production : ces gains sont un **plafond**, pas une estimation du skill",
        "   opérationnel, et la direction de l'écart n'est pas déterminable a priori. Le",
        "   ré-entraînement sur de vraies prévisions archivées interviendra après ~1 mois",
        "   de runs quotidiens ; ces chiffres seront alors remplacés.",
        "2. **Le forçage atmosphérique d'entraînement est parfait, celui de production",
        "   ne le sera pas.** Les features de forçage (vent 10 m et anomalie de pression",
        "   au niveau de la mer) sont apprises sur la **réanalyse ERA5** (0,25°, ECMWF,",
        "   connue après coup) et seront servies avec une **prévision ARPEGE",
        "   Europe** (0,1°, Météo-France), qui porte une erreur de lead time que la",
        "   réanalyse n'a pas. Ce n'est **pas** une équivalence : deux familles de",
        "   modèles, deux grilles, et une partie du gain ci-dessous ne survivra pas au",
        "   passage en opérationnel. Même catégorie de compromis que la réserve 1, et",
        "   même issue : il se résorbera quand le run quotidien aura accumulé assez de",
        "   ses propres prévisions pour ré-entraîner dessus. Détail dans",
        "   `docs/data-sources.md` §4bis.",
        "3. **Le gate de +5 % s'applique quand même**, mais il se lit",
        "   « +5 % mesuré sur analyse, avec un vent parfait », pas « +5 % en",
        "   opérationnel ».",
    ]
    # Tout est dérivé : la fermeté de la phrase suit les chiffres, elle ne les précède pas.
    inflated = [r for r in rows if r["gain"] > 0 and r["gain_debiased"] < 0.5 * r["gain"]]
    weak = [r["station"] for r in rows if r["weak"]]
    lines += [
        f"4. **Sur {len(inflated)} des {len(rows)} stations, plus de la moitié du gain",
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
    lines += _rejected_leads()
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text("\n".join(lines))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--test-days", type=int, default=30, help="issue days held out for test")
    ap.add_argument(
        "--model",
        choices=model.MODEL_NAMES,
        help="train only this candidate (default: train all three, publish the best "
        "per station on the gain hors biais)",
    )
    ap.add_argument(
        "--ablate",
        default="",
        metavar="COLS",
        help="comma-separated feature columns to zero — measures what they actually buy "
        f"(e.g. 'wind_u10,wind_v10'). Choices: {','.join(ABLATABLE)}",
    )
    args = ap.parse_args()

    ablate = tuple(c.strip() for c in args.ablate.split(",") if c.strip())
    if unknown := [c for c in ablate if c not in ABLATABLE]:
        ap.error(f"unknown feature column(s) {unknown} — pick from {ABLATABLE}")
    load_env()

    model_names = (args.model,) if args.model else model.MODEL_NAMES
    print(f"Training {', '.join(model_names)} (test = last {args.test_days} issue days):")
    if ablate:
        print(f"  ABLATION: {', '.join(ablate)} zeroed — artefacts and report NOT written")
    stations = load_stations()
    rows = [
        r
        for st in stations
        if (r := evaluate(st, args.test_days, ablate, model_names)) is not None
    ]
    if not rows:
        print("nothing trained")
        return 1

    if ablate:
        print(f"\nablation ({', '.join(ablate)} = 0), gain hors biais:")
        for r in rows:
            print(f"  {r['station']:16} {r['gain_debiased']:+8.1%}  MAE {r['mae_model']:.4f}")
        return 0

    # Machine-readable gate: the publisher (Task 8) reads this, not the markdown.
    # Merged, never rewritten from scratch: a station skipped this run (no
    # dataset on disk) keeps its previous artefact, so it must keep its verdict.
    known = {s.id for s in stations}
    previous = json.loads(GATE_PATH.read_text()) if GATE_PATH.exists() else {}
    gate = {k: v for k, v in previous.items() if k in known}  # drop retired stations
    for r in rows:
        entry = {
            "pass": r["pass"],
            "weak": r["weak"],
            "mae_model": round(r["mae_model"], 4),
            "mae_baseline": round(r["mae_base"], 4),
            "gain": round(r["gain"], 4),
            "gain_debiased": round(r["gain_debiased"], 4),
        }
        if r["baseline_model"]:
            entry["baseline_model"] = r["baseline_model"]
        gate[r["station"]] = entry
    GATE_PATH.write_text(json.dumps(gate, indent=2, sort_keys=True) + "\n")

    skipped = [s.id for s in stations if s.id not in {r["station"] for r in rows}]
    write_report(rows, args.test_days, skipped)
    failed = [r["station"] for r in rows if not r["pass"]]

    print(f"\nreport -> {REPORT_PATH}")
    print(f"gate: {len(rows) - len(failed)}/{len(rows)} PASS" + (f", FAIL: {failed}" if failed else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
