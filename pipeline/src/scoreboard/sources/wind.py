"""Open-Meteo 10 m wind fetcher — ERA5 for training, ARPEGE Europe for inference.

Both legs share one JSON contract and one parser, so the u/v convention seen at
training is byte-for-byte the one seen at inference. The *data* differs (ERA5
reanalysis vs ARPEGE forecast) — that skew is documented in
`docs/data-sources.md`; the *code path* does not.

Open-Meteo returns wind in the meteorological convention (the direction the wind
comes FROM). We convert to eastward/northward components once, here, because a
direction in degrees is circular and unusable as a raw model feature.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import requests

from scoreboard.config import Station
from scoreboard.sources import SourceError

WIND_COLUMNS = ["wind_u10", "wind_v10"]

_ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
_MODEL = "meteofrance_arpege_europe"
_HOURLY = "wind_speed_10m,wind_direction_10m"
_TIMEOUT = 30


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
    rad = np.deg2rad(df["direction"].to_numpy())
    speed = df["speed"].to_numpy()
    out = pd.DataFrame(
        {"wind_u10": -speed * np.sin(rad), "wind_v10": -speed * np.cos(rad)}, index=df.index
    )
    out.index.name = "time"
    return out[WIND_COLUMNS]


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
