"""Tests for the MFWAM baseline wave forecast fetcher."""

import os
from pathlib import Path

import pandas as pd
import pytest
import xarray as xr

from scoreboard.sources.mfwam import _extract_point, fetch_wave_forecast

_FIXTURE = Path(__file__).parent / "fixtures" / "mfwam_point.nc"


def test_extract_point_returns_hourly_hs_baseline_without_internal_nan():
    ds = xr.open_dataset(_FIXTURE)
    lat = float(ds["latitude"].values[0])
    lon = float(ds["longitude"].values[0])

    df = _extract_point(ds, lat, lon)

    assert list(df.columns) == ["hs_baseline"]
    assert df.index.name == "time"
    assert str(df.index.tz) == "UTC"
    # source is 3h resolution -> resampled to 1h should be denser
    assert len(df) > 17
    # hourly index, no gaps
    assert (df.index.to_series().diff().dropna() == pd.Timedelta("1h")).all()
    assert not df["hs_baseline"].isna().any()


@pytest.mark.skipif(
    not os.getenv("COPERNICUSMARINE_SERVICE_USERNAME"),
    reason="requires CMEMS credentials",
)
def test_fetch_wave_forecast_network():
    from datetime import date

    from scoreboard.config import Station

    station = Station(
        id="pierres-noires",
        name="Les Pierres Noires",
        kind="wave",
        lat=48.2903328,
        lon=-4.9683332,
        source="candhis",
        source_id="02911",
        baseline="mfwam",
    )
    result = fetch_wave_forecast([station], date.today())
    assert "pierres-noires" in result
    assert "hs_baseline" in result["pierres-noires"].columns
