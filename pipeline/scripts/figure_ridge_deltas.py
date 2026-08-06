"""Figure du post n°1 — écart boosting − ridge et son IC95 %, sur les 9 stations.

Les valeurs sont lues dans les tableaux « L'écart, avec sa barre d'erreur » de
`docs/plan-dev-modele.md`, seule trace datée de la mesure : rien n'est retapé
ici. Si le tableau change, la figure change avec — et si son format change, le
parse échoue bruyamment plutôt que de dessiner un chiffre faux.

matplotlib n'est pas une dépendance du pipeline — cette figure est un artefact
éditorial ponctuel, pas une étape du run quotidien. D'où le `--with` :

Run:  cd pipeline && uv run --with matplotlib python scripts/figure_ridge_deltas.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "docs" / "plan-dev-modele.md"
OUT = ROOT / "docs" / "editorial" / "figure-ecart-boosting-ridge.png"

NAVY = "#0E345D"
GREEN = "#1FA47A"
AMBER = "#E0A23B"
GREY = "#8A9BA8"

# | station | type | incumbent | gain ridge | gain inc | Δ | IC95 | conclusion |
ROW = re.compile(
    r"^\|\s*([a-z-]+)\s*\|\s*(tide|wind|wave)\s*\|.*?\|\s*"
    r"([+-][\d.]+) pt\s*\|\s*\[([+-][\d.]+) ; ([+-][\d.]+)\] pt\s*\|",
)

LABELS = {
    "brest": "Brest",
    "saint-malo": "Saint-Malo",
    "ouessant": "Ouessant",
    "dieppe": "Dieppe",
    "cherbourg-vent": "Cherbourg (vent)",
    "pierres-noires": "Les Pierres Noires",
    "belle-ile": "Belle-Île",
    "cherbourg": "Cherbourg (houle)",
    "anglet": "Anglet",
}


def parse(text: str) -> list[dict]:
    rows = []
    for line in text.splitlines():
        m = ROW.match(line)
        if m:
            sid, kind, delta, lo, hi = m.groups()
            rows.append(
                {
                    "id": sid,
                    "kind": kind,
                    "delta": float(delta),
                    "lo": float(lo),
                    "hi": float(hi),
                }
            )
    return rows


def classify(r: dict) -> tuple[str, str]:
    """(couleur, verdict) — jamais la couleur seule, cf. docs/brand.md."""
    if r["id"] == "anglet":
        # IC de largeur nulle sur 1 seule origine : absence de mesure, pas zéro.
        return AMBER, "indéterminé"
    if r["lo"] > 0:
        return GREEN, "boosting payé"
    return GREY, "boosting non payé"


def main() -> int:
    rows = parse(SOURCE.read_text(encoding="utf-8"))
    missing = set(LABELS) - {r["id"] for r in rows}
    if missing:
        print(f"stations absentes du tableau source : {sorted(missing)}", file=sys.stderr)
        return 1

    rows.sort(key=lambda r: r["delta"])
    y = range(len(rows))

    fig, ax = plt.subplots(figsize=(9, 5.4))
    seen = set()
    for i, r in enumerate(rows):
        color, verdict = classify(r)
        ax.errorbar(
            r["delta"],
            i,
            xerr=[[r["delta"] - r["lo"]], [r["hi"] - r["delta"]]],
            fmt="o",
            color=color,
            ecolor=color,
            elinewidth=2.2,
            capsize=4,
            markersize=7,
            markerfacecolor=color if verdict != "indéterminé" else "white",
            markeredgewidth=2,
            label=verdict if verdict not in seen else None,
        )
        seen.add(verdict)

    ax.axvline(0, color=NAVY, linewidth=1, linestyle="--", alpha=0.5)
    ax.set_yticks(list(y))
    ax.set_yticklabels(
        [f"{LABELS[r['id']]}  ({r['kind']})" for r in rows], fontsize=10
    )
    ax.set_xlabel(
        "écart entre les deux gains (points de %) — IC95 %, "
        "bootstrap apparié par jour d'émission",
        fontsize=9.5,
        color=NAVY,
    )
    ax.set_title(
        "Ce que le gradient boosting apporte face à une régression linéaire",
        fontsize=13,
        color=NAVY,
        pad=40,
        loc="left",
    )
    # Sans ça, « +12,9 » n'a pas d'ancrage : la figure ne montre pas les gains.
    # Le sens de lecture, lui, est déjà porté par la légende — ne pas le répéter.
    for i, line in enumerate(
        [
            "Gain = erreur (MAE) en moins face au modèle physique ; l'axe montre "
            "l'écart entre les deux gains.",
            "Ex. Saint-Malo : régression +21,2 %, boosting +34,1 % → écart de 12,9 pts.",
        ]
    ):
        ax.text(
            0,
            1.075 - 0.045 * i,
            line,
            transform=ax.transAxes,
            fontsize=8.5,
            color=GREY,
        )
    ax.set_ylim(-0.8, len(rows) - 0.2)
    # Note accrochée au point d'anglet : c'est lui qu'elle qualifie.
    anglet_y = next(i for i, r in enumerate(rows) if r["id"] == "anglet")
    ax.annotate(
        "intervalle de largeur nulle, sur une seule origine —\n"
        "absence de mesure, pas un écart nul",
        xy=(0.15, anglet_y),
        xytext=(1.2, anglet_y),
        fontsize=8.5,
        color=AMBER,
        va="center",
        arrowprops={"arrowstyle": "-", "color": AMBER, "alpha": 0.6, "linewidth": 1},
    )
    ax.legend(loc="lower right", frameon=False, fontsize=9, borderaxespad=1.2)
    ax.spines[["top", "right"]].set_visible(False)
    ax.grid(axis="x", alpha=0.15)
    fig.tight_layout()
    fig.savefig(OUT, dpi=200, facecolor="white")
    print(f"écrit : {OUT.relative_to(ROOT)} ({len(rows)} stations)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
