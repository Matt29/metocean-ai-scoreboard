"""Open-Meteo forcing fetcher: JSON parsing, u/v conversion, UTC alignment, errors."""

from datetime import date
from unittest.mock import Mock

import numpy as np
import pandas as pd
import pytest
import requests

from scoreboard.config import Station
from scoreboard.sources import SourceError
from scoreboard.sources.wind import (
    LEAD_DAYS,
    MULTI_FORCING_COLUMNS,
    STANDARD_PRESSURE_HPA,
    TIDE_FORCING_COLUMNS,
    WIND_MODELS,
    fetch_tide_forcing_history,
    fetch_wind_forecast,
    fetch_wind_models_forecast,
    fetch_wind_models_history,
    forcing_at_issue,
)

ST = Station(id="pierres-noires", name="PN", kind="wave", lat=48.29, lon=-4.97,
             source="candhis", source_id="02911", baseline="marine-best")


def payload(times, speeds, dirs, pressures=None):
    hourly = {"time": times, "wind_speed_10m": speeds, "wind_direction_10m": dirs}
    if pressures is not None:
        hourly["pressure_msl"] = pressures
    return {
        "hourly_units": {"wind_speed_10m": "m/s", "wind_direction_10m": "°"},
        "hourly": hourly,
    }


def make_session(body, status=200):
    s = Mock(); r = Mock()
    r.status_code = status
    r.json.return_value = body
    s.get.return_value = r
    return s


TIMES = ["2026-06-01T00:00", "2026-06-01T01:00", "2026-06-01T02:00", "2026-06-01T03:00"]
# Meteorological convention: direction is where the wind comes FROM.
DIRS = [0, 90, 180, 270]     # from N, from E, from S, from W
SPEEDS = [10.0, 10.0, 10.0, 10.0]
# Realistic MSL pressure, hPa - same length as TIMES.
PRESSURES = [1008.3, 1012.7, 1015.4, 1019.1]
# u eastward, v northward -> from N: (0,-10); from E: (-10,0); from S: (0,10); from W: (10,0)
EXPECTED_U = [0.0, -10.0, 0.0, 10.0]
EXPECTED_V = [-10.0, 0.0, 10.0, 0.0]
# pressure_anom = pressure_msl - STANDARD_PRESSURE_HPA
EXPECTED_PRESSURE_ANOM = [p - STANDARD_PRESSURE_HPA for p in PRESSURES]


def prev_payload(times, per_day):
    """Previous Runs reply: `per_day[k] = (speeds, dirs, pressures)` for lead day k."""
    hourly = {"time": times}
    for day, (speeds, dirs, pressures) in per_day.items():
        sfx = "" if day == 0 else f"_previous_day{day}"
        hourly[f"wind_speed_10m{sfx}"] = speeds
        hourly[f"wind_direction_10m{sfx}"] = dirs
        hourly[f"pressure_msl{sfx}"] = pressures
    return {"hourly": hourly}


# One lead day per wind speed, so a block is identifiable from the value alone.
PER_DAY = {day: ([10.0 + day] * 4, DIRS, PRESSURES) for day in LEAD_DAYS}


def test_forecast_parses_and_converts_to_uv():
    df = fetch_wind_forecast(ST, session=make_session(payload(TIMES, SPEEDS, DIRS, PRESSURES)))

    assert list(df.columns) == TIDE_FORCING_COLUMNS == [
        "wind_u10", "wind_v10", "pressure_anom", "dp_dt_3h", "dp_dt_6h",
    ]
    assert df.index.name == "time"
    assert str(df.index.tz) == "UTC"
    assert len(df) == 4
    assert np.allclose(df["wind_u10"], EXPECTED_U, atol=1e-9)
    assert np.allclose(df["wind_v10"], EXPECTED_V, atol=1e-9)
    assert np.allclose(df["pressure_anom"], EXPECTED_PRESSURE_ANOM, atol=1e-9)
    assert not df[["wind_u10", "wind_v10", "pressure_anom"]].isna().any().any()
    # 3 h tendency: only the last of these 4 hours has a 3 h-old neighbour.
    assert np.allclose(df["dp_dt_3h"], [np.nan, np.nan, np.nan, (1019.1 - 1008.3) / 3],
                       atol=1e-9, equal_nan=True)
    assert df["dp_dt_6h"].isna().all()  # no hour is 6 h into a 4 h payload


def test_one_request_per_station_with_every_lead_and_variable():
    """Open-Meteo has a free-tier quota: every lead and variable rides in one call."""
    session = make_session(prev_payload(TIMES, PER_DAY))
    fetch_tide_forcing_history(ST, date(2026, 6, 1), date(2026, 6, 2), session=session)
    assert session.get.call_count == 1
    hourly = session.get.call_args.kwargs["params"]["hourly"].split(",")
    assert set(hourly) == {
        f"{var}{'' if day == 0 else f'_previous_day{day}'}"
        for day in LEAD_DAYS
        for var in ("wind_speed_10m", "wind_direction_10m", "pressure_msl")
    }
    # Deeper leads exist on the API and must not be paid for: nothing reads past +48h.
    assert not any("previous_day3" in h for h in hourly)


def test_history_requests_ecmwf_ms_units_and_utc():
    session = make_session(prev_payload(TIMES, PER_DAY))
    fetch_tide_forcing_history(ST, date(2026, 6, 1), date(2026, 6, 2), session=session)
    params = session.get.call_args.kwargs["params"]
    # Same model as the serve leg, or training measures a run production never gets.
    assert params["models"] == "ecmwf_ifs025"
    assert params["wind_speed_unit"] == "ms"
    assert params["timezone"] == "UTC"
    assert params["start_date"] == "2026-06-01"
    assert params["end_date"] == "2026-06-02"


def test_history_returns_one_block_per_lead_day():
    df = fetch_tide_forcing_history(ST, date(2026, 6, 1), date(2026, 6, 1),
                                    session=make_session(prev_payload(TIMES, PER_DAY)))
    assert list(df.columns) == [f"{c}_d{day}" for day in LEAD_DAYS for c in TIDE_FORCING_COLUMNS]
    for day in LEAD_DAYS:
        assert np.allclose(df[f"wind_v10_d{day}"], [-(10.0 + day), 0.0, 10.0 + day, 0.0], atol=1e-9)


def test_forcing_at_issue_picks_the_run_the_issue_could_have_had():
    """+48h must be forced by a 2-day-old run, not by the freshest one."""
    times = [f"2026-06-0{d}T12:00" for d in (1, 2, 3)]
    df = fetch_tide_forcing_history(
        ST, date(2026, 6, 1), date(2026, 6, 3),
        session=make_session(prev_payload(times, {day: ([10.0 + day] * 3, [270] * 3, [1013.25] * 3)
                                                  for day in LEAD_DAYS})),
    )
    narrowed = forcing_at_issue(df, pd.Timestamp("2026-06-01T06:00", tz="UTC"))
    assert list(narrowed.columns) == TIDE_FORCING_COLUMNS
    # from W -> u = +speed; same day, +1 day, +2 days.
    assert np.allclose(narrowed["wind_u10"], [10.0, 11.0, 12.0], atol=1e-9)


def test_forcing_at_issue_drops_hours_past_the_deepest_lead():
    """Serving the oldest run at +96h would be a quiet lie; an absent hour trips
    the coverage floor instead."""
    times = [f"2026-06-0{d}T12:00" for d in (1, 5)]
    df = fetch_tide_forcing_history(
        ST, date(2026, 6, 1), date(2026, 6, 5),
        session=make_session(prev_payload(times, {day: ([10.0 + day] * 2, [270] * 2, [1013.25] * 2)
                                                  for day in LEAD_DAYS})),
    )
    narrowed = forcing_at_issue(df, pd.Timestamp("2026-06-01T06:00", tz="UTC"))
    assert list(narrowed.index) == [pd.Timestamp("2026-06-01T12:00", tz="UTC")]
    assert np.allclose(narrowed["wind_u10"], [10.0], atol=1e-9)


def test_forcing_at_issue_leaves_a_degraded_frame_to_the_coverage_floor():
    """None/empty/wrong-columns are `features.py`'s contract to reject, not this
    function's to crash on."""
    assert forcing_at_issue(None, pd.Timestamp("2026-06-01T06:00", tz="UTC")) is None


def test_forcing_at_issue_passes_a_non_stratified_frame_through():
    """The live serve leg and the multi-model frames take the same call, unchanged."""
    live = fetch_wind_forecast(ST, session=make_session(payload(TIMES, SPEEDS, DIRS, PRESSURES)))
    out = forcing_at_issue(live, pd.Timestamp("2026-06-01T06:00", tz="UTC"))
    assert out is live


def test_forecast_uses_ecmwf_model():
    session = make_session(payload(TIMES, SPEEDS, DIRS, PRESSURES))
    df = fetch_wind_forecast(ST, session=session)
    assert session.get.call_args.kwargs["params"]["models"] == "ecmwf_ifs025"
    assert list(df.columns) == TIDE_FORCING_COLUMNS
    assert str(df.index.tz) == "UTC"


def test_missing_hourly_values_are_dropped_not_nan():
    body = payload(TIMES, [10.0, None, 10.0, 10.0], [0, 90, None, 270], PRESSURES)
    df = fetch_wind_forecast(ST, session=make_session(body))
    # The tendency columns are excluded: this payload is 4 h long, shorter than
    # the 6 h window, so they are NaN throughout by construction.
    assert not df[["wind_u10", "wind_v10", "pressure_anom"]].isna().any().any()
    assert len(df) == 2


def test_duplicate_timestamps_are_dropped():
    """A duplicated index would blow up the nearest-reindex in features.py."""
    body = payload(TIMES + [TIMES[0]], SPEEDS + [3.0], DIRS + [45], PRESSURES + [PRESSURES[0]])
    df = fetch_wind_forecast(ST, session=make_session(body))
    assert not df.index.has_duplicates
    assert np.isclose(df["wind_u10"].iloc[0], EXPECTED_U[0])  # first wins, like candhis


def test_network_error_raises_source_error():
    s = Mock()
    s.get.side_effect = requests.ConnectionError("boom")
    with pytest.raises(SourceError):
        fetch_tide_forcing_history(ST, date(2026, 6, 1), date(2026, 6, 1), session=s)


def test_http_error_raises_source_error():
    body = {"error": True, "reason": "start_date is out of range"}
    with pytest.raises(SourceError):
        fetch_tide_forcing_history(ST, date(1800, 1, 1), date(1800, 1, 2),
                           session=make_session(body, status=400))


def test_malformed_payload_raises_source_error():
    with pytest.raises(SourceError):
        fetch_tide_forcing_history(ST, date(2026, 6, 1), date(2026, 6, 1),
                           session=make_session({"latitude": 48.29}))


def test_mono_model_missing_key_raises_not_empty_dataframe():
    """`hourly` present but missing `wind_speed_10m` must raise, not silently
    dropna() into an empty frame."""
    body = {"hourly": {"time": TIMES, "wind_direction_10m": DIRS}}
    with pytest.raises(SourceError):
        fetch_wind_forecast(ST, session=make_session(body))


def test_missing_lead_column_raises_not_a_silently_short_frame():
    """A lead absent from the payload must raise: narrowing would otherwise serve
    the freshest run at +48h, which is the exact skew this leg removes."""
    per_day = {day: PER_DAY[day] for day in LEAD_DAYS if day != LEAD_DAYS[-1]}
    with pytest.raises(SourceError):
        fetch_tide_forcing_history(ST, date(2026, 6, 1), date(2026, 6, 1),
                                   session=make_session(prev_payload(TIMES, per_day)))


def multi_payload(times, per_model):
    """per_model: dict[model] -> (speeds, dirs), suffixed like Open-Meteo's multi-model reply."""
    hourly = {"time": times}
    for model, (speeds, dirs) in per_model.items():
        hourly[f"wind_speed_10m_{model}"] = speeds
        hourly[f"wind_direction_10m_{model}"] = dirs
    return {"hourly": hourly}


def test_models_history_sends_all_models_to_the_historical_host():
    session = make_session(multi_payload(TIMES, {m: (SPEEDS, DIRS) for m in WIND_MODELS}))
    df = fetch_wind_models_history(ST, date(2025, 1, 1), date(2025, 1, 2), session=session)

    url = session.get.call_args.args[0] if session.get.call_args.args else \
        session.get.call_args.kwargs["url"]
    assert url == "https://historical-forecast-api.open-meteo.com/v1/forecast"
    params = session.get.call_args.kwargs["params"]
    assert params["models"] == ",".join(WIND_MODELS)
    assert params["wind_speed_unit"] == "ms"
    assert params["timezone"] == "UTC"
    assert params["start_date"] == "2025-01-01"
    assert params["end_date"] == "2025-01-02"
    assert list(df.columns) == MULTI_FORCING_COLUMNS


def test_models_history_converts_uv_per_model():
    session = make_session(multi_payload(TIMES, {m: (SPEEDS, DIRS) for m in WIND_MODELS}))
    df = fetch_wind_models_history(ST, date(2025, 1, 1), date(2025, 1, 2), session=session)

    for model in WIND_MODELS:
        assert np.allclose(df[f"wind_u10_{model}"], EXPECTED_U, atol=1e-9)
        assert np.allclose(df[f"wind_v10_{model}"], EXPECTED_V, atol=1e-9)


def test_models_forecast_uses_forecast_days_not_dates():
    session = make_session(multi_payload(TIMES, {m: (SPEEDS, DIRS) for m in WIND_MODELS}))
    fetch_wind_models_forecast(ST, session=session, forecast_days=5)

    url = session.get.call_args.args[0] if session.get.call_args.args else \
        session.get.call_args.kwargs["url"]
    assert url == "https://api.open-meteo.com/v1/forecast"
    params = session.get.call_args.kwargs["params"]
    assert params["forecast_days"] == 5
    assert "start_date" not in params and "end_date" not in params
    assert params["models"] == ",".join(WIND_MODELS)


def test_models_one_model_entirely_absent_stays_nan_not_zero():
    absent, present = WIND_MODELS[0], WIND_MODELS[1]
    session = make_session(multi_payload(TIMES, {present: (SPEEDS, DIRS)}))
    df = fetch_wind_models_history(ST, date(2025, 1, 1), date(2025, 1, 2), session=session)

    assert df[f"wind_u10_{absent}"].isna().all()
    assert df[f"wind_v10_{absent}"].isna().all()
    assert not df[f"wind_u10_{present}"].isna().any()


def test_models_one_model_all_null_stays_nan_not_zero():
    per_model = {m: (SPEEDS, DIRS) for m in WIND_MODELS}
    null_model = WIND_MODELS[0]
    per_model[null_model] = ([None] * len(TIMES), [None] * len(TIMES))
    session = make_session(multi_payload(TIMES, per_model))
    df = fetch_wind_models_history(ST, date(2025, 1, 1), date(2025, 1, 2), session=session)

    assert df[f"wind_u10_{null_model}"].isna().all()
    assert df[f"wind_v10_{null_model}"].isna().all()
    assert not (df[f"wind_u10_{null_model}"] == 0).any()


def test_pressure_tendency_is_taken_inside_a_run_block_never_across_two():
    """The trap this column exists to avoid.

    A tide forcing frame is stratified by run age, and `forcing_at_issue` hands a
    row from a *different* run on either side of a lead-day boundary. Runs differ
    by ~0.44 hPa at `_d1` and ~1.40 at `_d2` — the same order as a real 3 h
    tendency. A `.diff()` taken after the narrowing would therefore read the
    run-to-run departure as weather, twice per issue, at exactly the hours a
    depression is most likely to be moving. So the tendency is computed in the
    parser, inside each block, before any narrowing can mix two runs.

    The fixture makes the difference impossible to miss: every block rises by a
    clean 1 hPa/h, and each older run sits 20 hPa above the fresher one.
    """
    hours = pd.date_range("2026-06-01", periods=72, freq="1h", tz="UTC")
    times = [t.strftime("%Y-%m-%dT%H:%M") for t in hours]
    rising = np.arange(72.0)  # +1 hPa per hour, within every block
    per_day = {
        day: ([10.0] * 72, [270] * 72, list(1013.25 + rising + 20.0 * day)) for day in LEAD_DAYS
    }
    df = fetch_tide_forcing_history(
        ST, date(2026, 6, 1), date(2026, 6, 3), session=make_session(prev_payload(times, per_day))
    )
    narrowed = forcing_at_issue(df, pd.Timestamp("2026-06-01T06:00", tz="UTC"))

    # The block offset really is there: crossing into 2026-06-02 switches from
    # the freshest run to the one a day older, so the level jumps by 20 hPa on
    # top of the +1 hPa the hour itself brought. Without this the test below
    # would pass on a frame where the trap cannot even be sprung.
    boundary = narrowed.index.get_loc(pd.Timestamp("2026-06-02T00:00", tz="UTC"))
    step = narrowed["pressure_anom"].iloc[boundary] - narrowed["pressure_anom"].iloc[boundary - 1]
    assert np.isclose(step, 21.0)

    # And yet the tendency is 1 hPa/h everywhere, boundary hours included: it was
    # differenced inside its own block. A post-narrowing diff would read 21/3 = 7
    # here (and 21/6 = 3.5 on the 6 h column).
    tendency = narrowed.iloc[6:]  # the first 6 h have no 6 h-old neighbour
    assert np.allclose(tendency["dp_dt_3h"], 1.0, atol=1e-9)
    assert np.allclose(tendency["dp_dt_6h"], 1.0, atol=1e-9)


def test_pressure_tendency_spans_elapsed_hours_not_rows():
    """`.diff(3)` would mean three rows — three hours only while the leg is hourly."""
    times = ["2026-06-01T00:00", "2026-06-01T03:00", "2026-06-01T06:00", "2026-06-01T09:00"]
    df = fetch_wind_forecast(
        ST, session=make_session(payload(times, SPEEDS, DIRS, [1013.25, 1016.25, 1019.25, 1022.25]))
    )
    # 3 hPa every 3 h. Row-wise, the "3 h" column would span 9 h and read 1 hPa/h
    # against a true 1 hPa/h — indistinguishable here — but the "6 h" column has
    # no 6 h-old neighbour on this grid at all, and must stay NaN rather than
    # silently report the row six positions back.
    assert np.allclose(df["dp_dt_3h"].iloc[1:], 1.0, atol=1e-9)
    assert df["dp_dt_6h"].notna().sum() == 2  # 06:00 and 09:00 only
    assert np.allclose(df["dp_dt_6h"].iloc[2:], 1.0, atol=1e-9)
