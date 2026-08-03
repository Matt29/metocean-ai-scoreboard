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
from scoreboard.sources import SourceError, make_session

FORCING_COLUMNS = ["wind_u10", "wind_v10"]

# Task 0: the 3 wind models kept from the probe (>=90% coverage from 2025-06-01).
WIND_MODELS = ["meteofrance_arpege_europe", "ecmwf_ifs025", "icon_eu"]
MULTI_FORCING_COLUMNS = [f"{c}_{m}" for m in WIND_MODELS for c in ("wind_u10", "wind_v10")]

_ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
_HISTORICAL_URL = "https://historical-forecast-api.open-meteo.com/v1/forecast"
# Public (not `_MODEL`): `archive.py` records this as the served forecast's
# `source` column — it must name exactly the model `fetch_wind_forecast` calls.
FORECAST_MODEL = "meteofrance_arpege_europe"
_HOURLY = "wind_speed_10m,wind_direction_10m"
_TIMEOUT = 30

log = logging.getLogger(__name__)


def _parse_uv(hourly: dict, speed_key: str, dir_key: str) -> pd.DataFrame:
    """Wind speed/direction (meteorological convention, degrees FROM) -> u/v components.

    A key missing from the payload (model absent) or holding nulls (model
    100% null) stays NaN throughout — never zero-filled, so downstream
    coverage checks can refuse to serve on a dead model.
    """
    index = pd.to_datetime(hourly["time"], utc=True)
    speed = pd.to_numeric(pd.Series(hourly.get(speed_key), index=index), errors="coerce")
    direction = pd.to_numeric(pd.Series(hourly.get(dir_key), index=index), errors="coerce")
    rad = np.deg2rad(direction.to_numpy())
    speed = speed.to_numpy()
    out = pd.DataFrame(
        {"wind_u10": -speed * np.sin(rad), "wind_v10": -speed * np.cos(rad)}, index=index
    )
    out.index.name = "time"
    return out


def _get_payload(url: str, params: dict, station: Station, session) -> tuple[dict, dict]:
    session = session or make_session()
    try:
        resp = session.get(url, params=params, timeout=_TIMEOUT)
        payload = resp.json()
    except (requests.RequestException, ValueError) as exc:
        raise SourceError(station.id, f"open-meteo request failed: {exc}") from exc

    if resp.status_code != 200 or "hourly" not in payload:
        reason = payload.get("reason") if isinstance(payload, dict) else None
        raise SourceError(station.id, reason or f"open-meteo HTTP {resp.status_code}")

    hourly = payload["hourly"]
    if "time" not in hourly:
        raise SourceError(station.id, "open-meteo payload missing 'time'")
    return payload, hourly


def _log_resolved_cell(payload: dict, station: Station) -> None:
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


def _fetch(url: str, params: dict, station: Station, session) -> pd.DataFrame:
    payload, hourly = _get_payload(url, params, station, session)
    out = _parse_uv(hourly, "wind_speed_10m", "wind_direction_10m")
    out = out.dropna().sort_index()
    # Same guard as candhis.py: a duplicated index makes the nearest-reindex in
    # features.py raise instead of returning features.
    out = out[~out.index.duplicated(keep="first")]
    _log_resolved_cell(payload, station)
    return out[FORCING_COLUMNS]


def _fetch_models(url: str, params: dict, station: Station, session) -> pd.DataFrame:
    payload, hourly = _get_payload(url, params, station, session)
    parts = [
        _parse_uv(hourly, f"wind_speed_10m_{m}", f"wind_direction_10m_{m}").rename(
            columns={"wind_u10": f"wind_u10_{m}", "wind_v10": f"wind_v10_{m}"}
        )
        for m in WIND_MODELS
    ]
    out = pd.concat(parts, axis=1).sort_index()
    # Same guard as candhis.py: a duplicated index makes the nearest-reindex in
    # features.py raise instead of returning features.
    out = out[~out.index.duplicated(keep="first")]
    _log_resolved_cell(payload, station)
    return out[MULTI_FORCING_COLUMNS]


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
            "models": FORECAST_MODEL,
            "forecast_days": forecast_days,
            "wind_speed_unit": "ms",
            "timezone": "UTC",
        },
        station,
        session,
    )


def fetch_wind_models_history(
    station: Station, date_start: date, date_end: date, session: requests.Session | None = None
) -> pd.DataFrame:
    """Hourly 10 m wind from the 3 candidate models (Task 0) over [date_start, date_end]."""
    return _fetch_models(
        _HISTORICAL_URL,
        {
            "latitude": station.lat,
            "longitude": station.lon,
            "start_date": date_start.isoformat(),
            "end_date": date_end.isoformat(),
            "hourly": _HOURLY,
            "models": ",".join(WIND_MODELS),
            "wind_speed_unit": "ms",
            "timezone": "UTC",
        },
        station,
        session,
    )


def fetch_wind_models_forecast(
    station: Station, session: requests.Session | None = None, forecast_days: int = 3
) -> pd.DataFrame:
    """Hourly 10 m wind forecast from the 3 candidate models — covers the +48 h horizon."""
    return _fetch_models(
        _FORECAST_URL,
        {
            "latitude": station.lat,
            "longitude": station.lon,
            "hourly": _HOURLY,
            "models": ",".join(WIND_MODELS),
            "forecast_days": forecast_days,
            "wind_speed_unit": "ms",
            "timezone": "UTC",
        },
        station,
        session,
    )
