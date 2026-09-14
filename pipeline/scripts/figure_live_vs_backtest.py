"""Figure du post « 5 semaines réelles » — gain servi contre gain du backtest, par station.

Réel : `data/<station>/history.json` lu sur `origin/main` (le cron quotidien y
committe), jours `ok` non `backfilled` de la fenêtre `FIRST`..`LAST`, MAE
pondérée par heure scorée. Backtest : champ `gain` du `gate.json` de `GATE_REV`
— MAE brute, la même métrique que le réel. Jamais `gain_debiased` : comparer un
gain réel brut à un gain hors biais serait comparer deux métriques.

La fenêtre et `GATE_REV` vont ensemble : chaque jour servi doit l'avoir été par
les modèles que ce gate a jugés. `81a60d3` est le dernier état des modèles du
2026-08-04 ; la release pics `84a4473` les remplace dès l'émission du 2026-09-09.
Comparer à un gate plus récent confronterait les prévisions à un backtest qui
n'est pas celui de leur modèle.

Run:  cd pipeline && uv run --with matplotlib python scripts/figure_live_vs_backtest.py
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parents[2]
DATA_REV = "origin/main"
GATE_REV = "81a60d3"
FIRST, LAST = "2026-08-05", "2026-09-08"  # 08-04 : marée ré-entraînée après l’émission
OUT = ROOT / "docs" / "editorial" / "figure-5-semaines-reel-vs-backtest.png"

NAVY = "#0E345D"
GREY = "#6B7C89"  # assombri depuis #8A9BA8 : 2,79:1 sur blanc, sous 3:1
AMBER = "#B7791F"

LABELS = {
    "brest": "Brest (marée)",
    "saint-malo": "Saint-Malo (marée)",
    "ouessant": "Ouessant (vent)",
    "dieppe": "Dieppe (vent)",
    "cherbourg-vent": "Cherbourg (vent)",
    "pierres-noires": "Les Pierres Noires (houle)",
    "belle-ile": "Belle-Île (houle)",
    "anglet": "Anglet (houle)",
}


def git_json(rev: str, path: str):
    return json.loads(subprocess.check_output(["git", "-C", str(ROOT), "show", f"{rev}:{path}"]))


def live_gain(sid: str) -> tuple[float, int, str, str]:
    raw_days = git_json(DATA_REV, f"data/{sid}/history.json")["days"]
    days = [
        d for d in raw_days
        if d.get("status") == "ok" and not d.get("backfilled") and d.get("n_points")
        and FIRST <= d["date"] <= LAST
    ]
    n = sum(d["n_points"] for d in days)
    ia = sum(d["mae_ia"] * d["n_points"] for d in days) / n
    base = sum(d["mae_baseline"] * d["n_points"] for d in days) / n
    return (base - ia) / base, len(days), days[0]["date"], days[-1]["date"]


def main() -> int:
    sha = subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "--short", DATA_REV], text=True).strip()
    gate = git_json(GATE_REV, "pipeline/models/gate.json")
    rows = []
    for sid, label in LABELS.items():
        gain, n, first, last = live_gain(sid)
        rows.append({"id": sid, "label": label, "live": 100 * gain, "backtest": 100 * gate[sid]["gain"], "n": n})
        print(f"{sid:15} réel {100 * gain:+6.1f} % ({n} j, {first} → {last})  backtest {100 * gate[sid]['gain']:+6.1f} %")

    rows.sort(key=lambda r: r["live"])
    fig, ax = plt.subplots(figsize=(9, 5.6))
    for i, r in enumerate(rows):
        live_color = AMBER if r["id"] == "anglet" else NAVY
        ax.plot([r["backtest"], r["live"]], [i, i], color=GREY, linewidth=2, alpha=0.35, zorder=1)
        ax.scatter(r["backtest"], i, s=70, facecolor="white", edgecolor=GREY, linewidth=2, zorder=2,
                   label="backtest (critère de mise en ligne)" if i == 0 else None)
        ax.scatter(r["live"], i, s=80, color=live_color, zorder=3,
                   label="réel, prévisions servies" if i == len(rows) - 1 else None)
        right = max(r["live"], r["backtest"])
        note = "  dépubliée le 14/09" if r["id"] == "anglet" else ""
        ax.text(right + 1.5, i, f"{r['live']:+.0f} % réel · {r['backtest']:+.0f} % backtest{note}",
                va="center", fontsize=8.5, color=AMBER if note else NAVY)

    ax.axvline(0, color=NAVY, linewidth=1, linestyle="--", alpha=0.5)
    ax.set_yticks(range(len(rows)))
    ax.set_yticklabels([r["label"] for r in rows], fontsize=10, color=NAVY)
    ax.set_xlim(-10, 95)
    ax.set_ylim(-0.7, len(rows) - 0.3)
    ax.set_xlabel("erreur moyenne (MAE) en moins face au modèle physique, %", fontsize=9.5, color=NAVY)
    ax.set_title("5 semaines de prévisions réellement servies, face au backtest", fontsize=13,
                 color=NAVY, loc="left", pad=28)
    ax.text(0, 1.03, f"Émissions du {FIRST} au {LAST} · données {sha} · backtest {GATE_REV}",
            transform=ax.transAxes, fontsize=8.5, color=GREY)
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.13), ncol=2, frameon=False, fontsize=9)
    ax.spines[["top", "right"]].set_visible(False)
    ax.grid(axis="x", alpha=0.15)
    fig.tight_layout()
    fig.savefig(OUT, dpi=200, facecolor="white")
    print(f"écrit : {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
