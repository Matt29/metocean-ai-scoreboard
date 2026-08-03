"""Open-Meteo forcing fetcher: JSON parsing, u/v conversion, UTC alignment, errors."""

from datetime import date
from unittest.mock import Mock

import numpy as np
import pytest
import requests

from scoreboard.config import Station
from scoreboard.sources import SourceError
from scoreboard.sources.wind import FORCING_COLUMNS, fetch_wind_forecast, fetch_wind_history

ST = Station(id="pierres-noires", name="PN", kind="wave", lat=48.29, lon=-4.97,
             source="candhis", source_id="02911", baseline="mfwam")


def payload(times, speeds, dirs):
    return {
        "hourly_units": {"wind_speed_10m": "m/s", "wind_direction_10m": "°"},
        "hourly": {"time": times, "wind_speed_10m": speeds, "wind_direction_10m": dirs},
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
# u eastward, v northward -> from N: (0,-10); from E: (-10,0); from S: (0,10); from W: (10,0)
EXPECTED_U = [0.0, -10.0, 0.0, 10.0]
EXPECTED_V = [-10.0, 0.0, 10.0, 0.0]


def test_history_parses_and_converts_to_uv():
    df = fetch_wind_history(ST, date(2026, 6, 1), date(2026, 6, 1),
                            session=make_session(payload(TIMES, SPEEDS, DIRS)))

    assert list(df.columns) == FORCING_COLUMNS == ["wind_u10", "wind_v10"]
    assert df.index.name == "time"
    assert str(df.index.tz) == "UTC"
    assert len(df) == 4
    assert np.allclose(df["wind_u10"], EXPECTED_U, atol=1e-9)
    assert np.allclose(df["wind_v10"], EXPECTED_V, atol=1e-9)
    assert not df.isna().any().any()


def test_one_request_per_station_with_every_forcing_variable():
    """Open-Meteo has a free-tier quota: all variables ride in a single call."""
    session = make_session(payload(TIMES, SPEEDS, DIRS))
    fetch_wind_history(ST, date(2026, 6, 1), date(2026, 6, 2), session=session)
    assert session.get.call_count == 1
    hourly = session.get.call_args.kwargs["params"]["hourly"].split(",")
    assert set(hourly) == {"wind_speed_10m", "wind_direction_10m"}


def test_history_requests_ms_units_and_utc():
    session = make_session(payload(TIMES, SPEEDS, DIRS))
    fetch_wind_history(ST, date(2026, 6, 1), date(2026, 6, 2), session=session)
    params = session.get.call_args.kwargs["params"]
    assert params["wind_speed_unit"] == "ms"
    assert params["timezone"] == "UTC"
    assert params["start_date"] == "2026-06-01"
    assert params["end_date"] == "2026-06-02"


def test_forecast_uses_arpege_europe_model():
    session = make_session(payload(TIMES, SPEEDS, DIRS))
    df = fetch_wind_forecast(ST, session=session)
    assert session.get.call_args.kwargs["params"]["models"] == "meteofrance_arpege_europe"
    assert list(df.columns) == FORCING_COLUMNS
    assert str(df.index.tz) == "UTC"


def test_missing_hourly_values_are_dropped_not_nan():
    body = payload(TIMES, [10.0, None, 10.0, 10.0], [0, 90, None, 270])
    df = fetch_wind_history(ST, date(2026, 6, 1), date(2026, 6, 1), session=make_session(body))
    assert not df.isna().any().any()
    assert len(df) == 2


def test_duplicate_timestamps_are_dropped():
    """A duplicated index would blow up the nearest-reindex in features.py."""
    body = payload(TIMES + [TIMES[0]], SPEEDS + [3.0], DIRS + [45])
    df = fetch_wind_history(ST, date(2026, 6, 1), date(2026, 6, 1), session=make_session(body))
    assert not df.index.has_duplicates
    assert np.isclose(df["wind_u10"].iloc[0], EXPECTED_U[0])  # first wins, like candhis


def test_network_error_raises_source_error():
    s = Mock()
    s.get.side_effect = requests.ConnectionError("boom")
    with pytest.raises(SourceError):
        fetch_wind_history(ST, date(2026, 6, 1), date(2026, 6, 1), session=s)


def test_http_error_raises_source_error():
    body = {"error": True, "reason": "start_date is out of range"}
    with pytest.raises(SourceError):
        fetch_wind_history(ST, date(1800, 1, 1), date(1800, 1, 2),
                           session=make_session(body, status=400))


def test_malformed_payload_raises_source_error():
    with pytest.raises(SourceError):
        fetch_wind_history(ST, date(2026, 6, 1), date(2026, 6, 1),
                           session=make_session({"latitude": 48.29}))
