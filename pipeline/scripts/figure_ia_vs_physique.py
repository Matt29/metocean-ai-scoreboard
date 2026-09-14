"""Figure du post « 5 semaines réelles » — l'IA bat-elle la prévision physique ?

Réel : `data/<station>/history.json` lu sur `origin/main` (le cron quotidien y
committe), jours `ok` non `backfilled` de la fenêtre `FIRST`..`LAST`, MAE
pondérée par heure scorée. Gain = erreur moyenne en moins face à la prévision
physique servie le même jour.

`FIRST`..`LAST` : chaque jour a été servi par les mêmes modèles (marée
ré-entraînée le 2026-08-04 après l'émission du jour, release pics `84a4473`
dès l'émission du 2026-09-09). Houle : bouées Candhis muettes après le
2026-09-08 12:00.

Run:  cd pipeline && uv run --with matplotlib python scripts/figure_ia_vs_physique.py
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
FIRST, LAST = "2026-08-05", "2026-09-08"
OUT = ROOT / "docs" / "editorial" / "figure-5-semaines-ia-vs-physique.png"

NAVY = "#0E345D"
GREY = "#6B7C89"
AMBER = "#B7791F"

LABELS = {
    "brest": "Brest · marée",
    "saint-malo": "Saint-Malo · marée",
    "ouessant": "Ouessant · vent",
    "dieppe": "Dieppe · vent",
    "cherbourg-vent": "Cherbourg · vent",
    "pierres-noires": "Pierres Noires · houle",
    "belle-ile": "Belle-Île · houle",
    "anglet": "Anglet · houle",
}


def live_gain(sid: str) -> float:
    raw = subprocess.check_output(["git", "-C", str(ROOT), "show", f"{DATA_REV}:data/{sid}/history.json"])
    days = [
        d for d in json.loads(raw)["days"]
        if d.get("status") == "ok" and not d.get("backfilled") and d.get("n_points")
        and FIRST <= d["date"] <= LAST
    ]
    n = sum(d["n_points"] for d in days)
    ia = sum(d["mae_ia"] * d["n_points"] for d in days) / n
    base = sum(d["mae_baseline"] * d["n_points"] for d in days) / n
    return 100 * (base - ia) / base


def main() -> int:
    rows = sorted((live_gain(sid), label) for sid, label in LABELS.items())
    wins = sum(gain > 0 for gain, _ in rows)

    fig, ax = plt.subplots(figsize=(8, 5))
    for i, (gain, label) in enumerate(rows):
        color = NAVY if gain > 0 else AMBER
        ax.barh(i, gain, height=0.62, color=color)
        text = f"−{gain:.0f} % d'erreur" if gain > 0 else f"+{-gain:.0f} % d'erreur · retirée du site"
        ax.text(gain + 0.8 if gain > 0 else 0.8, i, text, va="center", fontsize=10.5, color=color)
        print(f"{label:24} {gain:+.1f} %")

    ax.set_yticks(range(len(rows)))
    ax.set_yticklabels([label for _, label in rows], fontsize=11, color=NAVY)
    ax.axvline(0, color=NAVY, linewidth=1)
    ax.set_xlim(-8, 50)
    ax.set_xticks([])
    ax.spines[["top", "right", "bottom", "left"]].set_visible(False)
    ax.tick_params(axis="y", length=0)
    fig.suptitle(f"L'IA bat la prévision physique sur {wins} stations sur {len(rows)}",
                 x=0.02, ha="left", fontsize=15, color=NAVY, fontweight="bold")
    fig.text(0.02, 0.895, "Erreur moyenne des prévisions sur 48 h, face au modèle physique\n"
             "Prévisions réellement servies chaque matin, du 5 août au 8 septembre 2026",
             ha="left", va="top", fontsize=10, color=GREY, linespacing=1.4)
    fig.tight_layout(rect=(0, 0, 1, 0.85))
    fig.savefig(OUT, dpi=200, facecolor="white")
    print(f"écrit : {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
