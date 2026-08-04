"""Open-Meteo atmospheric forcing fetcher — past ARPEGE runs for training, live
ARPEGE for inference.

One request per station. Both legs share one JSON contract and one parser, so
the conventions seen at training are byte-for-byte those seen at inference, and
since 2026-08-04 so is the *model*: the ERA5 reanalysis leg was deleted rather
than kept, because a reanalysis is the atmosphere as it turned out.

Residual optimism to keep in mind, and it is NOT fixed here: the Historical
Forecast API concatenates the freshest runs, so a past "forecast" is issued
hours — not 24 to 48 h — before its valid time. Measured 2026-08-04 at Brest,
its pressure correlates 0.9997 with ERA5. Training figures therefore still
overstate what a real +48 h forecast delivers; see `docs/plan-dev-modele.md`.

Open-Meteo returns wind in the meteorological convention (the direction the wind
comes FROM). We convert to eastward/northward components once, here, because a
direction in degrees is circular and unusable as a raw model feature.

`FORCING_COLUMNS` is deliberately generic (not `WIND_COLUMNS`): mean sea level
pressure rides alongside the wind on the single-model (tide) legs, as
`TIDE_FORCING_COLUMNS` — see that constant for why it is served there and only
there. Adding a forcing variable is one entry in the relevant `_HOURLY*`, one
column, and one entry in the relevant feature column list.
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

# Mean sea level pressure, as an anomaly to the standard atmosphere. Served on
# the single-model legs only — i.e. the `tide` path. The inverse barometer
# (~1 cm of water per hPa) is the first-order driver of the surge, which *is*
# the residual a tide station's model predicts; on a `wave` station it has no
# direct effect and Task 7C measured it costing 1 to 5 points there.
#
# It rode in `FORCING_COLUMNS` during Task 7C and was removed on 2026-08-03.
# Reinstated here for `tide` only, on new evidence: that measurement compared
# against the 90-day harmonic baseline, whose unresolved annual constituent left
# a seasonal drift in the residual. Pressure and that drift are both
# low-frequency, so the ablation could not separate them — the verdict was taken
# in the one regime where it was uninterpretable. See `docs/model-eval.md`.
STANDARD_PRESSURE_HPA = 1013.25
TIDE_FORCING_COLUMNS = [*FORCING_COLUMNS, "pressure_anom"]

# Task 0: the 3 wind models kept from the probe (>=90% coverage from 2025-06-01).
WIND_MODELS = ["meteofrance_arpege_europe", "ecmwf_ifs025", "icon_eu"]
MULTI_FORCING_COLUMNS = [f"{c}_{m}" for m in WIND_MODELS for c in ("wind_u10", "wind_v10")]

# Wind speed per model — the *baseline* candidates of a `kind="wind"` station,
# the exact mirror of `marine.MODEL_COLUMNS` for waves. Same payload as the u/v
# forcing above (Open-Meteo returns speed and direction in one response), so
# `with_speeds=True` costs no extra request: it only keeps a column the parser
# was already computing and discarding.
WIND_MODEL_COLUMNS = [f"ws_{m}" for m in WIND_MODELS]

_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
_HISTORICAL_URL = "https://historical-forecast-api.open-meteo.com/v1/forecast"
# Public (not `_MODEL`): `archive.py` records this as the served forecast's
# `source` column — it must name exactly the model `fetch_wind_forecast` calls.
FORECAST_MODEL = "meteofrance_arpege_europe"
_HOURLY = "wind_speed_10m,wind_direction_10m"
# The single-model legs (tide) ask for pressure too; the multi-model legs
# (wave/wind) do not, so no station pays for a variable its features exclude.
_HOURLY_TIDE = f"{_HOURLY},pressure_msl"
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
        {
            "wind_u10": -speed * np.sin(rad),
            "wind_v10": -speed * np.cos(rad),
            # Kept alongside u/v rather than recomputed as hypot(u, v): that
            # round-trip is lossy whenever direction is null while speed is not.
            "wind_speed": speed,
        },
        index=index,
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
    for key in ("wind_speed_10m", "wind_direction_10m"):
        if key not in hourly:
            raise SourceError(station.id, f"open-meteo payload missing {key!r}")
    out = _parse_uv(hourly, "wind_speed_10m", "wind_direction_10m")
    # Anomaly, not the raw hPa: the inverse barometer acts on the departure from
    # the standard atmosphere, and a column centred near zero shares the neutral
    # 0.0 fallback the other forcing columns already use for a gap.
    if "pressure_msl" not in hourly:
        raise SourceError(station.id, "open-meteo payload missing 'pressure_msl'")
    out["pressure_anom"] = (
        pd.to_numeric(pd.Series(hourly["pressure_msl"], index=out.index), errors="coerce")
        - STANDARD_PRESSURE_HPA
    )
    out = out.dropna().sort_index()
    # Same guard as candhis.py: a duplicated index makes the nearest-reindex in
    # features.py raise instead of returning features.
    out = out[~out.index.duplicated(keep="first")]
    _log_resolved_cell(payload, station)
    return out[TIDE_FORCING_COLUMNS]


def _fetch_models(
    url: str, params: dict, station: Station, session, with_speeds: bool = False
) -> pd.DataFrame:
    payload, hourly = _get_payload(url, params, station, session)
    parts = [
        _parse_uv(hourly, f"wind_speed_10m_{m}", f"wind_direction_10m_{m}").rename(
            columns={
                "wind_u10": f"wind_u10_{m}",
                "wind_v10": f"wind_v10_{m}",
                "wind_speed": f"ws_{m}",
            }
        )
        for m in WIND_MODELS
    ]
    out = pd.concat(parts, axis=1).sort_index()
    # Same guard as candhis.py: a duplicated index makes the nearest-reindex in
    # features.py raise instead of returning features.
    out = out[~out.index.duplicated(keep="first")]
    _log_resolved_cell(payload, station)
    columns = MULTI_FORCING_COLUMNS + WIND_MODEL_COLUMNS if with_speeds else MULTI_FORCING_COLUMNS
    return out[columns]


def fetch_wind_forecast_history(
    station: Station, date_start: date, date_end: date, session: requests.Session | None = None
) -> pd.DataFrame:
    """Past ARPEGE *forecasts* over [date_start, date_end] — the training twin of
    `fetch_wind_forecast`.

    Same model, same variables, same parser as the serve leg: what a `tide`
    station is trained on is what it will be served. The ERA5 *reanalysis* leg
    that used to live here was deleted on 2026-08-04: it is the atmosphere as it
    turned out, not as it was forecast, and training a surge model on it measures
    the ability to *reconstruct* a storm knowing what it did, which is not the
    job. `backfill.py` replays past days through this function too, so a
    backfilled day and a live day now share one forcing source.
    That skew was harmless while wind was a marginal feature; it stopped being
    harmless the moment pressure — the surge's first-order driver — became a
    dominant input. Measured 2026-08-04: on ERA5 forcing the model held 5.6 cm
    MAE straight through winter storms reaching 40 cm, a skill no forecast can
    deliver.

    Archive depth: ARPEGE forecasts are served from early 2023 (probed
    2026-08-04; 2022-09 returns all-null). Not the binding constraint for tide,
    whose rows already start `FIT_LOOKBACK_DAYS` after the first observation.
    """
    return _fetch(
        _HISTORICAL_URL,
        {
            "latitude": station.lat,
            "longitude": station.lon,
            "start_date": date_start.isoformat(),
            "end_date": date_end.isoformat(),
            "hourly": _HOURLY_TIDE,
            "models": FORECAST_MODEL,
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
            "hourly": _HOURLY_TIDE,
            "models": FORECAST_MODEL,
            "forecast_days": forecast_days,
            "wind_speed_unit": "ms",
            "timezone": "UTC",
        },
        station,
        session,
    )


def fetch_wind_models_history(
    station: Station,
    date_start: date,
    date_end: date,
    session: requests.Session | None = None,
    with_speeds: bool = False,
) -> pd.DataFrame:
    """Hourly 10 m wind from the 3 candidate models (Task 0) over [date_start, date_end].

    `with_speeds` adds `WIND_MODEL_COLUMNS` (the per-model speed a `kind="wind"`
    station uses as baseline candidates) to the same frame, from the same request.
    """
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
        with_speeds,
    )


def fetch_wind_models_forecast(
    station: Station,
    session: requests.Session | None = None,
    forecast_days: int = 3,
    with_speeds: bool = False,
    past_days: int = 2,
) -> pd.DataFrame:
    """Hourly 10 m wind forecast from the 3 candidate models — covers the +48 h horizon.

    `past_days` matters for the same reason it does in `marine.fetch_wave_models_forecast`:
    when this frame *is* the baseline (a `kind="wind"` station), the serve path reads it
    backwards from `t0` to build `last_err` / `mean_err_24h`. Without history before
    00:00 UTC the 24 h error window would be averaged over 6 h at serve time and over a
    full 24 h at train time — the exact train/serve skew this project keeps paying for.
    Harmless on the wave path, which only reads this frame forward as forcing.
    """
    return _fetch_models(
        _FORECAST_URL,
        {
            "latitude": station.lat,
            "longitude": station.lon,
            "hourly": _HOURLY,
            "models": ",".join(WIND_MODELS),
            "forecast_days": forecast_days,
            "past_days": past_days,
            "wind_speed_unit": "ms",
            "timezone": "UTC",
        },
        station,
        session,
        with_speeds,
    )
