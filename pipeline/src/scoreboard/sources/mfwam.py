"""CMEMS MFWAM baseline wave forecast fetcher (VHM0, one subset for all stations)."""

import tempfile
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import xarray as xr

from scoreboard.config import Station
from scoreboard.sources import SourceError

_DATASET_ID = "cmems_mod_glo_wav_anfc_0.083deg_PT3H-i"
_VARIABLE = "VHM0"
_BBOX_MARGIN = 0.2


def _extract_point(ds: xr.Dataset, lat: float, lon: float) -> pd.DataFrame:
    """Nearest-point extraction + 3h -> 1h interpolation, single hs_baseline column."""
    point = ds[_VARIABLE].sel(latitude=lat, longitude=lon, method="nearest")
    df = point.to_dataframe(name="hs_baseline")[["hs_baseline"]]
    df.index = pd.to_datetime(df.index, utc=True)
    df.index.name = "time"
    df = df.sort_index()
    df = df.resample("1h").interpolate()
    return df


def fetch_wave_forecast(stations: list[Station], run_date: date) -> dict[str, pd.DataFrame]:
    wave_stations = [s for s in stations if s.kind == "wave"]
    if not wave_stations:
        return {}

    import copernicusmarine

    lats = [s.lat for s in wave_stations]
    lons = [s.lon for s in wave_stations]

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            response = copernicusmarine.subset(
                dataset_id=_DATASET_ID,
                variables=[_VARIABLE],
                minimum_longitude=min(lons) - _BBOX_MARGIN,
                maximum_longitude=max(lons) + _BBOX_MARGIN,
                minimum_latitude=min(lats) - _BBOX_MARGIN,
                maximum_latitude=max(lats) + _BBOX_MARGIN,
                start_datetime=run_date.isoformat(),
                end_datetime=(run_date + timedelta(days=2)).isoformat(),
                output_directory=tmpdir,
                output_filename="mfwam_subset.nc",
            )
            nc_path = Path(response.output_directory) / response.file_path
            ds = xr.open_dataset(nc_path).load()
    except Exception as exc:  # noqa: BLE001 - any CMEMS/network/auth failure
        raise SourceError("mfwam", f"mfwam subset failed: {exc}") from exc

    return {s.id: _extract_point(ds, s.lat, s.lon) for s in wave_stations}
