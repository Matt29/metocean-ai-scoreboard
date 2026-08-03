"""Open-Meteo atmospheric forcing fetcher — ERA5 for training, ARPEGE for inference.

One request per station. Both legs share one JSON contract and one parser, so
the conventions seen at training are byte-for-byte those seen at inference. The
*data* differs (ERA5 reanalysis vs ARPEGE forecast) — that skew is documented in
`docs/data-sources.md`; the *code path* does not.

Open-Meteo returns wind in the meteorological convention (the direction the wind
comes FROM). We convert to eastward/northward components once, here, because a
direction in degrees is circular and unusable as a raw model feature.

`FORCING_COLUMNS` is deliberately generic (not `WIND_COLUMNS`): mean sea level
pressure rode here in Task 7C, was measured non-contributive and removed — see
`docs/model-eval.md`. Adding a forcing variable is one entry in `_HOURLY`, one
column, and one entry in `FEATURE_COLUMNS`.
"""

from __future__ import annotations

import logging
from datetime import date

import numpy as np
import pandas as pd
import requests

from scoreboard.config import Station
from scoreboard.sources import SourceError

FORCING_COLUMNS = ["wind_u10", "wind_v10"]

_ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
_MODEL = "meteofrance_arpege_europe"
_HOURLY = "wind_speed_10m,wind_direction_10m"
_TIMEOUT = 30

log = logging.getLogger(__name__)


def _fetch(url: str, params: dict, station: Station, session) -> pd.DataFrame:
    session = session or requests.Session()
    try:
        resp = session.get(url, params=params, timeout=_TIMEOUT)
        payload = resp.json()
    except (requests.RequestException, ValueError) as exc:
        raise SourceError(station.id, f"open-meteo request failed: {exc}") from exc

    if resp.status_code != 200 or "hourly" not in payload:
        reason = payload.get("reason") if isinstance(payload, dict) else None
        raise SourceError(station.id, reason or f"open-meteo HTTP {resp.status_code}")

    hourly = payload["hourly"]
    try:
        df = pd.DataFrame(
            {
                "time": pd.to_datetime(hourly["time"], utc=True),
                "speed": pd.to_numeric(hourly["wind_speed_10m"], errors="coerce"),
                "direction": pd.to_numeric(hourly["wind_direction_10m"], errors="coerce"),
            }
        )
    except KeyError as exc:
        raise SourceError(station.id, f"open-meteo payload missing {exc}") from exc

    df = df.dropna().set_index("time").sort_index()
    # Same guard as candhis.py: a duplicated index makes the nearest-reindex in
    # features.py raise instead of returning features.
    df = df[~df.index.duplicated(keep="first")]

    # Open-Meteo snaps to its own grid (ERA5 0.25 deg, ARPEGE 0.1 deg). Log the
    # resolved cell: a distant or non-zero-elevation cell is land-contaminated,
    # exactly the bug already fixed for MFWAM in 0740e81. `elevation` is the
    # cheapest land signal the API exposes.
    grid_lat, grid_lon = payload.get("latitude"), payload.get("longitude")
    if grid_lat is not None and grid_lon is not None:
        log.info(
            "%s: forcing cell (%.3f, %.3f) vs station (%.3f, %.3f), offset %.3f deg, elevation %s m",
            station.id, grid_lat, grid_lon, station.lat, station.lon,
            max(abs(grid_lat - station.lat), abs(grid_lon - station.lon)),
            payload.get("elevation"),
        )

    rad = np.deg2rad(df["direction"].to_numpy())
    speed = df["speed"].to_numpy()
    out = pd.DataFrame(
        {"wind_u10": -speed * np.sin(rad), "wind_v10": -speed * np.cos(rad)}, index=df.index
    )
    out.index.name = "time"
    return out[FORCING_COLUMNS]


def fetch_wind_history(
    station: Station, date_start: date, date_end: date, session: requests.Session | None = None
) -> pd.DataFrame:
    """Hourly ERA5 10 m wind over [date_start, date_end] — one request per station."""
    return _fetch(
        _ARCHIVE_URL,
        {
            "latitude": station.lat,
            "longitude": station.lon,
            "start_date": date_start.isoformat(),
            "end_date": date_end.isoformat(),
            "hourly": _HOURLY,
            "wind_speed_unit": "ms",
            "timezone": "UTC",
        },
        station,
        session,
    )


def fetch_wind_forecast(
    station: Station, session: requests.Session | None = None, forecast_days: int = 3
) -> pd.DataFrame:
    """Hourly ARPEGE Europe 10 m wind forecast — covers the +48 h horizon."""
    return _fetch(
        _FORECAST_URL,
        {
            "latitude": station.lat,
            "longitude": station.lon,
            "hourly": _HOURLY,
            "models": _MODEL,
            "forecast_days": forecast_days,
            "wind_speed_unit": "ms",
            "timezone": "UTC",
        },
        station,
        session,
    )
