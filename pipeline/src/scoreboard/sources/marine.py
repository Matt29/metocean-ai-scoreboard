"""Open-Meteo Marine API fetcher — the 5 wave models, live and historical.

Same JSON contract for both legs (train = historical, serve = live), same
parser — the anti-skew guarantee of `sources/wind.py`, applied to waves.
Replaces `mfwam.py`/CMEMS on the wave path (Task 6 wires it in).
A model absent from the archive answers HTTP 200 with nulls: columns are kept
as NaN so downstream coverage checks (features.py) can refuse to serve — they
must NEVER be silently zero-filled here.

Key format confirmed by Task 0 (raw probe, see task-0-coverage.md): a
multi-model request (this module always requests all 5) suffixes every
`hourly` key with `_<model>`, e.g. `wave_height_gwam`.
"""
from __future__ import annotations

import logging
from datetime import date

import pandas as pd
import requests

from scoreboard.config import Station
from scoreboard.sources import SourceError, make_session

WAVE_MODELS = ["meteofrance_wave", "ecmwf_wam025", "gwam", "ewam", "ncep_gfswave025"]
MODEL_COLUMNS = [f"hs_{m}" for m in WAVE_MODELS]
_MARINE_URL = "https://marine-api.open-meteo.com/v1/marine"
_TIMEOUT = 60

log = logging.getLogger(__name__)


def _fetch(params: dict, station: Station, session) -> pd.DataFrame:
    session = session or make_session()
    try:
        resp = session.get(_MARINE_URL, params=params, timeout=_TIMEOUT)
        payload = resp.json()
    except (requests.RequestException, ValueError) as exc:
        raise SourceError(station.id, f"marine request failed: {exc}") from exc
    if resp.status_code != 200 or "hourly" not in payload:
        reason = payload.get("reason") if isinstance(payload, dict) else None
        raise SourceError(station.id, reason or f"marine HTTP {resp.status_code}")

    hourly = payload["hourly"]
    out = pd.DataFrame(index=pd.to_datetime(hourly["time"], utc=True))
    out.index.name = "time"
    for model in WAVE_MODELS:
        vals = hourly.get(f"wave_height_{model}")
        out[f"hs_{model}"] = (
            pd.to_numeric(pd.Series(vals, index=out.index), errors="coerce")
            if vals is not None
            else float("nan")
        )
    out = out.sort_index()
    # Same guard as candhis.py/wind.py: a duplicated index makes the
    # nearest-reindex in features.py raise instead of returning features.
    out = out[~out.index.duplicated(keep="first")]

    # Open-Meteo snaps to its own grid (ERA5 0.25 deg, ARPEGE 0.1 deg). Log the
    # resolved cell: a distant or non-zero-elevation cell is land-contaminated,
    # exactly the bug already fixed for MFWAM in 0740e81. `elevation` is the
    # cheapest land signal the API exposes.
    grid_lat, grid_lon = payload.get("latitude"), payload.get("longitude")
    if grid_lat is not None and grid_lon is not None:
        log.info(
            "%s: wave cell (%.3f, %.3f) vs station (%.3f, %.3f), offset %.3f deg, elevation %s m",
            station.id, grid_lat, grid_lon, station.lat, station.lon,
            max(abs(grid_lat - station.lat), abs(grid_lon - station.lon)),
            payload.get("elevation"),
        )

    return out


def fetch_wave_models_history(
    station: Station,
    date_start: date,
    date_end: date,
    session: requests.Session | None = None,
) -> pd.DataFrame:
    """Hourly wave height (m) from the 5 wave models over [date_start, date_end]."""
    return _fetch(
        {
            "latitude": station.lat,
            "longitude": station.lon,
            "start_date": date_start.isoformat(),
            "end_date": date_end.isoformat(),
            "hourly": "wave_height",
            "models": ",".join(WAVE_MODELS),
            "timezone": "UTC",
        },
        station,
        session,
    )


def fetch_wave_models_forecast(
    station: Station,
    session: requests.Session | None = None,
    forecast_days: int = 3,
    past_days: int = 2,
) -> pd.DataFrame:
    """Hourly wave height (m) forecast from the 5 wave models — covers the +48 h horizon.

    `past_days` is not optional garnish: the serve path reads this same frame
    *backwards* from `t0` to build `last_err` and `mean_err_24h` (see
    `features.build_features`). Without it Open-Meteo starts the grid at today
    00:00 UTC, i.e. 6 h before a 06:00 issue — the 24 h error window would then
    be averaged over those 6 h alone, while training assembles it over the full
    24 h from an archive with complete history. That silent train/serve skew is
    exactly the class of bug this project has already paid for; 2 days leaves
    margin for a later issue hour without a second request.
    """
    return _fetch(
        {
            "latitude": station.lat,
            "longitude": station.lon,
            "hourly": "wave_height",
            "models": ",".join(WAVE_MODELS),
            "forecast_days": forecast_days,
            "past_days": past_days,
            "timezone": "UTC",
        },
        station,
        session,
    )
