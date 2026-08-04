"""Open-Meteo forcing fetcher: JSON parsing, u/v conversion, UTC alignment, errors."""

from datetime import date
from unittest.mock import Mock

import numpy as np
import pytest
import requests

from scoreboard.config import Station
from scoreboard.sources import SourceError
from scoreboard.sources.wind import (
    MULTI_FORCING_COLUMNS,
    STANDARD_PRESSURE_HPA,
    TIDE_FORCING_COLUMNS,
    WIND_MODELS,
    fetch_wind_forecast,
    fetch_wind_forecast_history,
    fetch_wind_models_forecast,
    fetch_wind_models_history,
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


def test_history_parses_and_converts_to_uv():
    df = fetch_wind_forecast_history(ST, date(2026, 6, 1), date(2026, 6, 1),
                            session=make_session(payload(TIMES, SPEEDS, DIRS, PRESSURES)))

    assert list(df.columns) == TIDE_FORCING_COLUMNS == ["wind_u10", "wind_v10", "pressure_anom"]
    assert df.index.name == "time"
    assert str(df.index.tz) == "UTC"
    assert len(df) == 4
    assert np.allclose(df["wind_u10"], EXPECTED_U, atol=1e-9)
    assert np.allclose(df["wind_v10"], EXPECTED_V, atol=1e-9)
    assert np.allclose(df["pressure_anom"], EXPECTED_PRESSURE_ANOM, atol=1e-9)
    assert not df.isna().any().any()


def test_one_request_per_station_with_every_forcing_variable():
    """Open-Meteo has a free-tier quota: all variables ride in a single call."""
    session = make_session(payload(TIMES, SPEEDS, DIRS, PRESSURES))
    fetch_wind_forecast_history(ST, date(2026, 6, 1), date(2026, 6, 2), session=session)
    assert session.get.call_count == 1
    hourly = session.get.call_args.kwargs["params"]["hourly"].split(",")
    assert set(hourly) == {"wind_speed_10m", "wind_direction_10m", "pressure_msl"}


def test_history_requests_ms_units_and_utc():
    session = make_session(payload(TIMES, SPEEDS, DIRS, PRESSURES))
    fetch_wind_forecast_history(ST, date(2026, 6, 1), date(2026, 6, 2), session=session)
    params = session.get.call_args.kwargs["params"]
    assert params["wind_speed_unit"] == "ms"
    assert params["timezone"] == "UTC"
    assert params["start_date"] == "2026-06-01"
    assert params["end_date"] == "2026-06-02"


def test_forecast_uses_arpege_europe_model():
    session = make_session(payload(TIMES, SPEEDS, DIRS, PRESSURES))
    df = fetch_wind_forecast(ST, session=session)
    assert session.get.call_args.kwargs["params"]["models"] == "meteofrance_arpege_europe"
    assert list(df.columns) == TIDE_FORCING_COLUMNS
    assert str(df.index.tz) == "UTC"


def test_missing_hourly_values_are_dropped_not_nan():
    body = payload(TIMES, [10.0, None, 10.0, 10.0], [0, 90, None, 270], PRESSURES)
    df = fetch_wind_forecast_history(ST, date(2026, 6, 1), date(2026, 6, 1), session=make_session(body))
    assert not df.isna().any().any()
    assert len(df) == 2


def test_duplicate_timestamps_are_dropped():
    """A duplicated index would blow up the nearest-reindex in features.py."""
    body = payload(TIMES + [TIMES[0]], SPEEDS + [3.0], DIRS + [45], PRESSURES + [PRESSURES[0]])
    df = fetch_wind_forecast_history(ST, date(2026, 6, 1), date(2026, 6, 1), session=make_session(body))
    assert not df.index.has_duplicates
    assert np.isclose(df["wind_u10"].iloc[0], EXPECTED_U[0])  # first wins, like candhis


def test_network_error_raises_source_error():
    s = Mock()
    s.get.side_effect = requests.ConnectionError("boom")
    with pytest.raises(SourceError):
        fetch_wind_forecast_history(ST, date(2026, 6, 1), date(2026, 6, 1), session=s)


def test_http_error_raises_source_error():
    body = {"error": True, "reason": "start_date is out of range"}
    with pytest.raises(SourceError):
        fetch_wind_forecast_history(ST, date(1800, 1, 1), date(1800, 1, 2),
                           session=make_session(body, status=400))


def test_malformed_payload_raises_source_error():
    with pytest.raises(SourceError):
        fetch_wind_forecast_history(ST, date(2026, 6, 1), date(2026, 6, 1),
                           session=make_session({"latitude": 48.29}))


def test_mono_model_missing_key_raises_not_empty_dataframe():
    """`hourly` present but missing `wind_speed_10m` must raise, not silently
    dropna() into an empty frame."""
    body = {"hourly": {"time": TIMES, "wind_direction_10m": DIRS}}
    with pytest.raises(SourceError):
        fetch_wind_forecast_history(ST, date(2026, 6, 1), date(2026, 6, 1), session=make_session(body))


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
