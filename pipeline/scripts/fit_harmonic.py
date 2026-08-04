#!/usr/bin/env python
"""Ajuste et persiste les constantes harmoniques -> models/<station>-harmonic.joblib.

Run:  cd pipeline && uv run python scripts/fit_harmonic.py [--station brest]

**Le run quotidien n'a pas besoin de ce script** : il ré-ajuste tout seul dès que
les constantes dépassent `harmonic.REFIT_DAYS` (`daily._ensure_harmonic`). Cette
CLI ne sert qu'à **forcer un ajustement hors cadence** (test local, ou reprise
après un incident). Elle ne sert pas à amorcer une station neuve : sans artefact,
`_ensure_harmonic` ajuste de lui-même au premier run. Elle appelle exactement la
fonction de production, pour qu'ajuster à la main et ajuster en production ne
puissent jamais diverger.

L'artefact n'est pas régénérable à l'identique plus tard : il dépend des obs
disponibles le jour du fit. Il se commite, comme `models/<station>.joblib`.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date

from scoreboard import daily
from scoreboard.config import load_env, load_stations
from scoreboard.sources import SourceError


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--station", help="n'ajuster que cette station (défaut : toutes les marées)")
    args = ap.parse_args()
    load_env()

    stations = [s for s in load_stations() if s.kind == "tide"]
    if args.station:
        stations = [s for s in stations if s.id == args.station]
    if not stations:
        print("aucune station de marée à ajuster", file=sys.stderr)
        return 1

    failed = False
    for st in stations:
        try:
            fitted = daily.refit_harmonic(st, date.today())
        except SourceError as exc:
            print(f"  {st.id}: {exc}", file=sys.stderr)
            failed = True
            continue
        print(f"  {st.id}: fit daté du {fitted.fitted_at:%Y-%m-%d} -> "
              f"{daily.harmonic.artifact_path(st.id)}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
