import json
from datetime import date
from pathlib import Path
from unittest.mock import Mock

import pytest

from scoreboard.config import Station
from scoreboard.sources import SourceError
from scoreboard.sources.waterlevel import fetch_tide_obs

FIX = json.loads((Path(__file__).parent / "fixtures/refmar_data.json").read_text())
ST = Station(id="brest", name="Brest", kind="tide", lat=48.38, lon=-4.5,
             source="shom", source_id="3", baseline="harmonic")


def make_session(payload, status=200):
    s = Mock(); r = Mock()
    r.status_code = status; r.json.return_value = payload
    s.get.return_value = r
    return s


def test_parses_refmar_payload_to_hourly_utc():
    df = fetch_tide_obs(ST, date(2026, 7, 29), session=make_session(FIX))
    assert list(df.columns) == ["level"]
    assert df.index.name == "time"
    assert df.index.tz is not None and str(df.index.tz) == "UTC"
    assert (df["level"] > -15).all() and (df["level"] < 15).all()
    # native cadence is 10 min -> resampled hourly means fewer rows than raw fixture
    assert len(df) < len(FIX["data"])


def test_failure_raises_source_error():
    bad = {"data": []}
    with pytest.raises(SourceError):
        fetch_tide_obs(ST, date(2026, 7, 29), session=make_session(bad, status=500))
