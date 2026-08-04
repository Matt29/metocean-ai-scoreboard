"""Open-Meteo atmospheric forcing fetcher — past runs for training, live runs
for inference.

One request per station. Both legs share one JSON contract and one parser, so
the conventions seen at training are byte-for-byte those seen at inference, and
since 2026-08-04 so is the *model*: the ERA5 reanalysis leg was deleted rather
than kept, because a reanalysis is the atmosphere as it turned out.

The residual optimism that survived that fix is closed here, on the tide leg,
2026-08-04. The Historical Forecast API concatenates the freshest runs, so a
past "forecast" was issued hours — not 24 to 48 h — before its valid time
(measured at Brest: its pressure correlates 0.9997 with ERA5). A surge model
trained on it was scored on forcing no +48 h forecast can deliver. The tide leg
therefore moved to the **Previous Runs API** (`fetch_tide_forcing_history`),
which serves runs stratified by age, and to `ecmwf_ifs025` — the only model that
API stratifies (ARPEGE returns 0 % on every `previous_day` wind column, probed
2026-08-04). The wave and wind legs stay on the Historical Forecast API: there
the forcing is a secondary input and their baseline is itself a forecast that
degrades with it, so the same skew does not fall on the model alone
(`docs/plan-dev-modele.md`).

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

# Pressure tendency, tide only. The surge does not answer to the local pressure
# alone but to the *movement* of the depression: dP/dt is also a proxy for the
# offshore wind field the station never sees.
#
# Measured 2026-08-04 (`--ablate dp_dt_3h,dp_dt_6h`, paired bootstrap over issue
# days): brest **+2.83 %** off-bias, 95 % CI [+2.16, +3.51], P(delta<=0) = 0 %;
# saint-malo +0.17 %, indistinguishable from zero, CI [-0.30, +0.66]. The mirror
# image of `features.TIDE_RATE_COLUMN`, which carries saint-malo and not brest —
# one station answers to the system moving, the other to its own tidal phase.
#
# It is computed HERE, in the parser, and not in `features.py` — the one place
# where it is safe. On the training leg the forcing frame is stratified by run
# age (`fetch_tide_forcing_history`), and `forcing_at_issue` hands a row from a
# different run block on either side of a lead-day boundary. A `.diff()` taken
# after that narrowing would straddle two runs at each boundary and measure the
# run-to-run departure (0.44 hPa at `_d1`, 1.40 at `_d2` — see that fetcher)
# instead of the weather. Taken here, each block is differenced within itself
# and the narrowing carries the result along like any other forcing column.
PRESSURE_TENDENCY_H = (3, 6)
PRESSURE_TENDENCY_COLUMNS = [f"dp_dt_{h}h" for h in PRESSURE_TENDENCY_H]
TIDE_FORCING_COLUMNS = [*FORCING_COLUMNS, "pressure_anom", *PRESSURE_TENDENCY_COLUMNS]

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
_PREVIOUS_RUNS_URL = "https://previous-runs-api.open-meteo.com/v1/forecast"
# Public (not `_MODEL`): `archive.py` records this as the served forecast's
# `source` column — it must name exactly the model `fetch_wind_forecast` calls.
# ECMWF and not ARPEGE since 2026-08-04, for one reason only: the Previous Runs
# API that makes the *training* forcing honest serves stratified runs for
# `ecmwf_ifs025` and not for ARPEGE. Serving a model the training leg cannot
# replay would put back the skew this whole change removes.
TIDE_FORECAST_MODEL = "ecmwf_ifs025"
_HOURLY = "wind_speed_10m,wind_direction_10m"
_TIDE_VARIABLES = ("wind_speed_10m", "wind_direction_10m", "pressure_msl")
# The single-model legs (tide) ask for pressure too; the multi-model legs
# (wave/wind) do not, so no station pays for a variable its features exclude.
_HOURLY_TIDE = ",".join(_TIDE_VARIABLES)
_TIMEOUT = 30
# Half an hour: the tendency lookup must land on the intended hour or on nothing.
# A wider window would quietly return the adjacent hour and label a 2 h departure
# as a 3 h one.
_TENDENCY_TOLERANCE = pd.Timedelta("30min")

# Lead days a `HORIZON_H = 48` issue can reach: the issue's own day, and the two
# after it. Deeper `previous_day` columns exist (up to 7) and are deliberately
# not requested — no tide feature ever reads beyond +48 h.
LEAD_DAYS = (0, 1, 2)
# Archive walls, both probed 2026-08-04 and both the same kind of fact: the first
# date Open-Meteo actually serves what a leg asks for. They live here, beside the
# fetchers they describe, rather than beside `build_dataset`'s clamp — a third leg
# should find its siblings, not a comment trail across two files. Requesting
# earlier does not lengthen training: it fabricates issues that `features.py`'s
# coverage floor then rejects one by one.
#   tide: first date `previous_day2` is served for ECMWF (2024-02-04 all-null).
#   multi-model: first date the 3 wind models are served together without a hole.
TIDE_FORCING_START = date(2024, 2, 5)
WIND_MODELS_START = date(2024, 2, 3)

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


def _lead_suffix(lead_day: int) -> str:
    """Open-Meteo's variable suffix for a run `lead_day` days older than the freshest."""
    return "" if lead_day == 0 else f"_previous_day{lead_day}"


def _tide_frame(hourly: dict, station: Station, suffix: str = "") -> pd.DataFrame:
    """`TIDE_FORCING_COLUMNS` read off one set of payload keys.

    `suffix` selects which run: `""` is the freshest one, `"_previous_dayN"` the
    one N days older (Previous Runs API). One parser for both, so a stratified
    lead and a live forecast cannot drift apart in their conventions.
    """
    for var in _TIDE_VARIABLES:
        if f"{var}{suffix}" not in hourly:
            raise SourceError(station.id, f"open-meteo payload missing {var + suffix!r}")
    out = _parse_uv(hourly, f"wind_speed_10m{suffix}", f"wind_direction_10m{suffix}")
    # Anomaly, not the raw hPa: the inverse barometer acts on the departure from
    # the standard atmosphere, and a column centred near zero shares the neutral
    # 0.0 fallback the other forcing columns already use for a gap.
    out["pressure_anom"] = (
        pd.to_numeric(pd.Series(hourly[f"pressure_msl{suffix}"], index=out.index), errors="coerce")
        - STANDARD_PRESSURE_HPA
    )
    # Backward tendency, hPa/h, over an *elapsed* window rather than a row count:
    # `.diff(3)` would silently mean three rows, which is three hours only as long
    # as every leg stays hourly. Left NaN on the leading hours — no tendency is
    # knowable there, and inventing a 0.0 would read as a settled barometer. The
    # live leg carries a day of past hours so the served horizon never lands on
    # them (see `fetch_wind_forecast`), and the training frame loses its first six
    # hours out of two years.
    # Sorted and deduplicated for the lookup only: `_finalize` does it for the
    # frame, but it runs after this and `reindex` raises on a duplicate label.
    p = out["pressure_anom"]
    p = p[~p.index.duplicated(keep="first")].sort_index()
    for hours in PRESSURE_TENDENCY_H:
        shifted = p.reindex(
            out.index - pd.Timedelta(hours=hours), method="nearest", tolerance=_TENDENCY_TOLERANCE
        )
        out[f"dp_dt_{hours}h"] = (out["pressure_anom"].to_numpy() - shifted.to_numpy()) / hours
    return out[TIDE_FORCING_COLUMNS]


def _finalize(out: pd.DataFrame, payload: dict, station: Station) -> pd.DataFrame:
    """The tail every fetcher shares: sort, drop duplicate hours, log the cell.

    Same guard as candhis.py: a duplicated index makes the nearest-reindex in
    features.py raise instead of returning features.
    """
    out = out.sort_index()
    out = out[~out.index.duplicated(keep="first")]
    _log_resolved_cell(payload, station)
    return out


def _fetch(url: str, params: dict, station: Station, session) -> pd.DataFrame:
    payload, hourly = _get_payload(url, params, station, session)
    # Dropped on the measured columns only. The tendency is NaN on the leading
    # hours by construction, and those hours carry a perfectly good wind and
    # pressure — deleting the row would throw away three good columns to hide one
    # unknowable. A tendency hole inside the served horizon is caught where every
    # other forcing hole is: the per-column coverage floor in `features.py`.
    return _finalize(
        _tide_frame(hourly, station).dropna(subset=[*FORCING_COLUMNS, "pressure_anom"]),
        payload,
        station,
    )


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
    out = _finalize(pd.concat(parts, axis=1), payload, station)
    columns = MULTI_FORCING_COLUMNS + WIND_MODEL_COLUMNS if with_speeds else MULTI_FORCING_COLUMNS
    return out[columns]


def fetch_tide_forcing_history(
    station: Station, date_start: date, date_end: date, session: requests.Session | None = None
) -> pd.DataFrame:
    """Past ECMWF forecasts over [date_start, date_end], **stratified by run age**
    — the training twin of `fetch_wind_forecast`.

    Returns a *wide* frame: `TIDE_FORCING_COLUMNS` suffixed `_d0`, `_d1`, `_d2`,
    one block per entry in `LEAD_DAYS`. `_d0` is the freshest run for that valid
    time, `_d1` the run one day older, `_d2` two days older. Callers never read
    it directly — `forcing_at_issue` picks the block a given issue could have
    had. This is what makes a +48 h training row a +48 h *forecast* rather than
    a near-analysis: see the module docstring for why the previous leg
    (Historical Forecast API) could not.

    Measured at Brest over December 2025, against the freshest run: MSL pressure
    departs by 0.44 hPa at `_d1` and 1.40 hPa at `_d2`; 10 m wind speed by 0.54
    and 1.05 m/s. That spread is the forecast error the model must now live
    with, and used not to see at all.

    Archive depth: `TIDE_FORCING_START` — this is now the binding constraint on
    a tide dataset, ahead of `FIT_LOOKBACK_DAYS`.
    """
    hourly = [
        f"{var}{_lead_suffix(day)}" for day in LEAD_DAYS for var in _TIDE_VARIABLES
    ]
    payload, payload_hourly = _get_payload(
        _PREVIOUS_RUNS_URL,
        {
            "latitude": station.lat,
            "longitude": station.lon,
            "start_date": date_start.isoformat(),
            "end_date": date_end.isoformat(),
            "hourly": ",".join(hourly),
            "models": TIDE_FORECAST_MODEL,
            "wind_speed_unit": "ms",
            "timezone": "UTC",
        },
        station,
        session,
    )
    # No `dropna` across the blocks: a hole in one lead must not delete the other
    # leads' hours. `features.py` already refuses to serve on the column it
    # actually reads, per issue, which is the finer-grained guard.
    return _finalize(
        pd.concat(
            [
                _tide_frame(payload_hourly, station, _lead_suffix(day)).add_suffix(f"_d{day}")
                for day in LEAD_DAYS
            ],
            axis=1,
        ),
        payload,
        station,
    )


def forcing_at_issue(forcing: pd.DataFrame, t0: pd.Timestamp) -> pd.DataFrame:
    """The run an issue at `t0` could actually have had, as `TIDE_FORCING_COLUMNS`.

    Each valid time takes the block whose age matches its distance from `t0` in
    whole days: same day -> `_d0`, next day -> `_d1`, and so on, clamped to
    `LEAD_DAYS`. Open-Meteo stratifies by day and not by run, so `_d0` is the
    freshest run of the issue's own day — leads under ~18 h therefore keep a
    little of the old optimism, while everything past 24 h (where the gain was
    being overstated) is now a genuine day-old forecast.

    Valid times past the deepest lead are dropped, not clamped: serving a
    2-day-old run at +96 h would be a quiet lie, whereas an absent hour trips
    `features.py`'s coverage floor and the station is marked missing. Nothing
    reads that far today (`HORIZON_H` is 48 h), so this is a guard, not a limit.

    Pass-through for anything that is not a stratified frame: the live serve
    leg, the multi-model wave/wind frames, and the degraded inputs
    `features.py` is contracted to reject itself (None, empty, wrong columns)
    all go through untouched, so callers have one call and no branch.
    """
    if f"{TIDE_FORCING_COLUMNS[0]}_d{LEAD_DAYS[0]}" not in getattr(forcing, "columns", []):
        return forcing
    # The stratified frame spans years and this runs once per issue; only the
    # issue's own horizon is ever read from the result, so bound the work to it.
    forcing = forcing.loc[t0.normalize() : t0.normalize() + pd.Timedelta(days=len(LEAD_DAYS))]
    days = (forcing.index.normalize() - t0.normalize()).days
    # LEAD_DAYS is 0..N, so a clamped day offset indexes `blocks` directly.
    picked = np.clip(days, LEAD_DAYS[0], LEAD_DAYS[-1])
    blocks = np.stack(
        [forcing[[f"{c}_d{day}" for c in TIDE_FORCING_COLUMNS]].to_numpy() for day in LEAD_DAYS]
    )
    rows = blocks[picked, np.arange(len(forcing))]
    return pd.DataFrame(rows, index=forcing.index, columns=TIDE_FORCING_COLUMNS)


def fetch_wind_forecast(
    station: Station, session: requests.Session | None = None, forecast_days: int = 3,
    past_days: int = 1,
) -> pd.DataFrame:
    """Hourly ECMWF IFS 10 m wind + MSL pressure forecast — covers the +48 h horizon.

    `past_days` is here for the same reason as in `fetch_wind_models_forecast`, one
    variable further along: `pressure_anom` now carries a 3 h and a 6 h tendency,
    and the first hours of any frame have none. Without a day of past hours a run
    issued shortly after 00:00 UTC would serve NaN tendencies over the start of its
    own horizon — while the training frame, spanning two years, never does. The
    training leg needs no equivalent: it loses six hours out of two years.
    """
    return _fetch(
        _FORECAST_URL,
        {
            "latitude": station.lat,
            "longitude": station.lon,
            "hourly": _HOURLY_TIDE,
            "models": TIDE_FORECAST_MODEL,
            "forecast_days": forecast_days,
            "past_days": past_days,
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
