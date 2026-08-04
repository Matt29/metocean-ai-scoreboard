#!/usr/bin/env python
"""Regenerate `docs/editorial/figure-brest-surcote.png` — the post's lead image.

Run:  cd pipeline && uv run --with matplotlib python scripts/figure_surge.py

One forecast issue and its 48 h, twice: the water level (observation, harmonic
prediction, model) on top, the surge alone underneath. The surge panel is the
whole point — it is the quantity the model actually predicts, and the only one
where the two curves can be told apart at a glance.

**Why this script exists.** The first version of the figure was produced by hand
and the post ended up quoting numbers from a model that no longer existed, with
no way to check them short of redoing the plot from memory. A figure that states
a measurement is a claim like any other: it has to be reproducible from the
repository, and it has to be regenerated whenever the artefact changes.

The emission is picked **on the observation, never on the model's performance**:
the day of the largest absolute surge in the test window. That is a storm case
and not an average day — the post says so, and must keep saying so. It resolves
to 2026-01-22 today; it is recomputed rather than hardcoded so the figure stays
honest if the test window moves, and the resolved date is printed for the caption.

Matplotlib is not a pipeline dependency (`--with matplotlib`): nothing in
production draws, and a plotting stack has no business in the daily run's
resolution.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "pipeline" / "scripts"))
sys.path.insert(0, str(ROOT / "pipeline" / "src"))

import train as T
from scoreboard import model
from scoreboard.config import load_stations

OUT = ROOT / "docs" / "editorial" / "figure-brest-surcote.png"
STATION = "brest"
ISSUE_HOUR = 6

# Ocean Data Consulting tokens, resolved to values here because matplotlib cannot
# read CSS custom properties. Source of truth stays
# `~/Documents/DEV/WEB/ODC_WEBSITE/DESIGN_SYSTEM/tokens/colors.css` — see
# `docs/brand.md`. Semantic role in the comment, so a token change is greppable.
NAVY_900 = "#0A2540"  # --text-strong
NAVY_600 = "#1C5E9A"  # --text-link, the harmonic
CYAN_600 = "#28A6C2"  # --brand-accent, the model
SLATE_700 = "#3A4D5C"  # --text-body
SLATE_500 = "#6B8190"  # --text-muted
SLATE_300 = "#BCCAD6"  # grid
SLATE_50 = "#F4F8FA"  # --surface-page
WHITE = "#FFFFFF"

# Written out rather than taken from the locale: `%B` follows LC_TIME, which is
# whatever the machine running this happens to have. The label must not depend on
# the shell that generated the figure.
#
# The figure is labelled in **English** while the post that carries it is in
# French. That is deliberate, decided 2026-08-04: the image travels further than
# its caption. Do not "fix" it back.
MONTHS = ("January", "February", "March", "April", "May", "June", "July",
          "August", "September", "October", "November", "December")


def pick_issue(x: pd.DataFrame, obs: pd.Series) -> pd.Timestamp:
    """Issue day carrying the largest |surge| — chosen on the observation alone."""
    resid = obs.to_numpy() - x["baseline"].to_numpy()
    day = T.issue_days(x)
    worst = pd.Series(np.abs(resid)).groupby(day.to_numpy()).max().idxmax()
    return pd.Timestamp(worst) + pd.Timedelta(hours=ISSUE_HOUR)


def main() -> int:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.ticker import MultipleLocator

    station = next(s for s in load_stations() if s.id == STATION)
    x, obs, is_test, _ = T._tide_data(station, T.TEST_DAYS_BY_KIND["tide"])
    xte, ote = x[is_test], obs[is_test]

    t0 = pick_issue(xte, ote)
    rows = T.issue_days(xte) == t0.normalize()
    xi, oi = xte[rows], ote[rows]
    order = np.argsort(xi["lead_h"].to_numpy())
    xi, oi = xi.iloc[order], oi.iloc[order]

    level_model = T._levels(model.load(STATION), xi, "tide")
    harmonic = xi["baseline"].to_numpy()
    observed = oi.to_numpy()
    lead = xi["lead_h"].to_numpy()

    mae_h = float(np.abs(observed - harmonic).mean())
    mae_m = float(np.abs(observed - level_model).mean())
    peak_obs = float(np.max(np.abs(observed - harmonic)))
    peak_mod = float((level_model - harmonic)[np.argmax(np.abs(observed - harmonic))])

    fig, (ax1, ax2) = plt.subplots(
        2, 1, figsize=(10, 8.2), sharex=True, height_ratios=[3, 2],
        gridspec_kw={"hspace": 0.13},
    )
    fig.patch.set_facecolor(WHITE)

    for ax in (ax1, ax2):
        ax.set_facecolor(SLATE_50)
        ax.grid(True, color=SLATE_300, linewidth=0.6, alpha=0.7)
        ax.set_axisbelow(True)
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)
        for side in ("left", "bottom"):
            ax.spines[side].set_color(SLATE_300)
        ax.tick_params(colors=SLATE_500, labelsize=9)
        ax.xaxis.set_major_locator(MultipleLocator(6))

    ax1.plot(lead, observed, color=NAVY_900, lw=2.4, label="Observed water level (SHOM tide gauge)")
    ax1.plot(lead, harmonic, color=NAVY_600, lw=1.8, ls="--", label="Physical forecast (harmonic tide)")
    ax1.plot(lead, level_model, color=CYAN_600, lw=2.2, label="AI model (post-processed physics)")
    ax1.set_ylabel("Water level (m)", color=SLATE_700, fontsize=10)
    ax1.legend(
        loc="upper left", frameon=True, facecolor=WHITE, edgecolor=SLATE_300,
        fontsize=9, labelcolor=SLATE_700, framealpha=1.0,
    )
    ax1.set_title(
        f"Brest — one forecast issue, its 48 hours\n"
        f"Issued {t0.day} {MONTHS[t0.month - 1]} {t0.year} at {t0:%H} UTC",
        color=NAVY_900, fontsize=13, fontweight="bold", loc="left", pad=14,
    )

    # The surge alone: what the model is actually asked to predict. Same colours
    # as above so the eye carries the mapping down from one panel to the other.
    ax2.axhline(0, color=SLATE_500, lw=1)
    ax2.fill_between(lead, 0, observed - harmonic, color=NAVY_900, alpha=0.10)
    ax2.plot(lead, observed - harmonic, color=NAVY_900, lw=2.4, label="Observed storm surge")
    ax2.plot(lead, level_model - harmonic, color=CYAN_600, lw=2.2, label="Storm surge predicted by the AI")
    ax2.set_ylabel("Storm surge (m)", color=SLATE_700, fontsize=10)
    ax2.set_xlabel("Forecast lead time (hours)", color=SLATE_700, fontsize=10)
    ax2.legend(
        loc="upper left", frameon=True, facecolor=WHITE, edgecolor=SLATE_300,
        fontsize=9, labelcolor=SLATE_700, framealpha=1.0,
    )
    ax2.set_xlim(lead.min(), lead.max())

    # Three short lines rather than one long one. Twice now the caption has run
    # off the right edge, and both times the half that got cut was the half
    # saying how the case was chosen — i.e. the honest half. Short lines cannot
    # overflow silently; a long one can, and did.
    for y, text, color, size in (
        (0.085,
         f"Over these 48 h: {mae_h * 100:.0f} cm mean error for the physics alone, "
         f"{mae_m * 100:.0f} cm for the model.", SLATE_700, 9),
        (0.055,
         "Issue chosen on the observation — the largest storm surge of the test year —",
         SLATE_500, 8.5),
        (0.025,
         "never on the model's performance. This is a storm case, not an average day.",
         SLATE_500, 8.5),
    ):
        fig.text(0.012, y, text, color=color, fontsize=size)
    # `tight_layout(rect=...)` was not keeping the x-axis title above the caption
    # — it does not know about `fig.text`, and the label kept landing on the first
    # line. An explicit bottom margin is the one thing it cannot argue with.
    fig.tight_layout()
    fig.subplots_adjust(bottom=0.15)
    fig.savefig(OUT, dpi=200, facecolor=WHITE)

    print(f"{OUT.relative_to(ROOT)}")
    print(f"  émission      : {t0:%Y-%m-%d %H:%M} UTC")
    print(f"  MAE 48 h      : physique {mae_h * 100:.1f} cm -> modèle {mae_m * 100:.1f} cm")
    print(f"  pic de surcote: observé {peak_obs * 100:.0f} cm, prédit {peak_mod * 100:.0f} cm")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
