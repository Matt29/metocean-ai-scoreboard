#!/usr/bin/env python
"""One-shot coverage probe for the multi-model retrain (Task 0).

Counts non-null values per station x model over the target window and prints
the exact hourly key names returned by multi-model requests. Read-only, no
artefacts — the numbers go into the task-0 report by hand.
"""
from __future__ import annotations

from datetime import date, timedelta

from scoreboard.config import load_env, load_stations
from scoreboard.sources import make_session

WAVE_MODELS = ["meteofrance_wave", "ecmwf_wam025", "gwam", "ewam", "ncep_gfswave025"]
WIND_MODELS = ["meteofrance_arpege_europe", "ecmwf_ifs025", "icon_eu"]
MARINE_URL = "https://marine-api.open-meteo.com/v1/marine"
WIND_HIST_URL = "https://historical-forecast-api.open-meteo.com/v1/forecast"
START, END = date(2025, 1, 1), date.today() - timedelta(days=1)


def probe(session, url, params, label):
    payload = session.get(url, params=params, timeout=60).json()
    if "hourly" not in payload:
        print(f"{label}: ERROR {payload.get('reason')}")
        return
    hourly = payload["hourly"]
    n = len(hourly["time"])
    for key, vals in sorted(hourly.items()):
        if key == "time":
            continue
        nn = sum(v is not None for v in vals)
        print(f"{label} | {key}: {nn}/{n} ({nn / n:.1%})")


def main() -> int:
    load_env()
    session = make_session()
    common = {"start_date": START.isoformat(), "end_date": END.isoformat(), "timezone": "UTC"}
    for st in [s for s in load_stations() if s.kind == "wave"]:
        point = {"latitude": st.lat, "longitude": st.lon}
        probe(session, MARINE_URL,
              {**point, **common, "hourly": "wave_height", "models": ",".join(WAVE_MODELS)},
              f"marine {st.id}")
        probe(session, WIND_HIST_URL,
              {**point, **common, "hourly": "wind_speed_10m,wind_direction_10m",
               "models": ",".join(WIND_MODELS), "wind_speed_unit": "ms"},
              f"wind {st.id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
