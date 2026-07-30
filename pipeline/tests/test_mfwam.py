"""Tests for the MFWAM baseline wave forecast fetcher."""

import os
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import xarray as xr

from scoreboard.sources.mfwam import _extract_point, fetch_wave_forecast

_FIXTURE = Path(__file__).parent / "fixtures" / "mfwam_point.nc"


def _synthetic_ds(land_value=np.nan) -> xr.Dataset:
    """3x3 grid, land cell (0,0) = all-NaN, ocean cells = constant valid values."""
    times = pd.date_range("2026-07-30", periods=3, freq="3h", tz=None)
    lats = [48.0, 48.083, 48.166]
    lons = [-5.0, -4.917, -4.834]
    data = np.full((3, 3, 3), 1.5)  # (time, lat, lon)
    data[:, 0, 0] = land_value  # nearest cell to (48.0, -5.0) is land-masked
    return xr.Dataset(
        {"VHM0": (("time", "latitude", "longitude"), data)},
        coords={"time": times, "latitude": lats, "longitude": lons},
    )


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


def test_extract_point_skips_land_masked_nearest_cell():
    ds = _synthetic_ds()

    df = _extract_point(ds, lat=48.0, lon=-5.0)  # nearest cell is the land-masked one

    assert not df["hs_baseline"].isna().any()
    assert (df["hs_baseline"] == 1.5).all()


def test_extract_point_raises_when_no_valid_cell_within_radius():
    ds = _synthetic_ds()
    # blank out every cell so no valid neighbor exists at all
    ds["VHM0"][:] = np.nan

    with pytest.raises(ValueError, match="no valid"):
        _extract_point(ds, lat=48.0, lon=-5.0)


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
