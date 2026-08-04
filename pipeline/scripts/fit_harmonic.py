#!/usr/bin/env python
"""Ajuste et persiste les constantes harmoniques -> models/<station>-harmonic.joblib.

Run:  cd pipeline && uv run python scripts/fit_harmonic.py [--station brest]

À relancer tous les `harmonic.REFIT_DAYS` jours (180) : passé cet âge, le run
quotidien refuse de servir la station plutôt que de publier une baseline périmée
(`daily._baseline_window`). C'est le seul endroit du pipeline de production qui
paie encore les deux ans de REFMAR (~50 requêtes, ~160 Mo par station) — une
fois par semestre au lieu d'une fois par jour.

L'artefact n'est pas régénérable à l'identique plus tard : il dépend des obs
disponibles le jour du fit. Il se commite, comme `models/<station>.joblib`.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta

from scoreboard import harmonic
from scoreboard.config import load_env, load_stations
from scoreboard.sources.waterlevel import fetch_tide_obs


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--station", help="n'ajuster que cette station (défaut : toutes les marées)")
    args = ap.parse_args()
    load_env()

    end = date.today() + timedelta(days=1)
    start = end - timedelta(days=harmonic.FIT_LOOKBACK_DAYS)
    stations = [s for s in load_stations() if s.kind == "tide"]
    if args.station:
        stations = [s for s in stations if s.id == args.station]
    if not stations:
        print("aucune station de marée à ajuster", file=sys.stderr)
        return 1

    failed = False
    for st in stations:
        obs = fetch_tide_obs(st, start, date_end=end)
        level = obs["level"].dropna()
        # Le plancher porte sur la *couverture* de la fenêtre, pas sur un compte
        # d'heures exact : REFMAR a des trous (4,2 % à Brest sur 2025-2026, 0,05 %
        # à Saint-Malo), donc exiger 17520 h pleines sur une demande de 730 jours
        # ne serait jamais satisfait. Ce qui compte est que la fenêtre *couvre*
        # deux ans — sous `FIT_LOOKBACK_DAYS` utide ne sépare ni S2/K2 ni Sa et
        # rend des amplitudes fausses sans jamais lever. Une station neuve reste
        # sans artefact — donc `missing` au quotidien — jusqu'à ses deux ans.
        floor = int(0.9 * 24 * harmonic.FIT_LOOKBACK_DAYS)
        if len(level) < floor:
            print(
                f"  {st.id}: seulement {len(level)}h d'obs (< {floor}h) — pas d'ajustement",
                file=sys.stderr,
            )
            failed = True
            continue
        model = harmonic.fit(level, st.lat)
        path = harmonic.artifact_path(st.id)
        model.save(path)
        print(f"  {st.id}: {len(level)}h d'obs, fit daté du {model.fitted_at:%Y-%m-%d} -> {path}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
