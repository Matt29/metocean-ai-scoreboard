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
_MAX_CELL_RADIUS = 3  # nearest-valid-cell search radius, in grid cells (land-mask fallback)


def _nearest_valid_cell(da: xr.DataArray, lat: float, lon: float) -> tuple[int, int]:
    """Index of the closest grid cell (within _MAX_CELL_RADIUS) whose series isn't all-NaN."""
    lat_vals = da["latitude"].values
    lon_vals = da["longitude"].values
    lat0 = int(abs(lat_vals - lat).argmin())
    lon0 = int(abs(lon_vals - lon).argmin())

    best = None  # (dist2, i, j)
    for i in range(max(lat0 - _MAX_CELL_RADIUS, 0), min(lat0 + _MAX_CELL_RADIUS + 1, len(lat_vals))):
        for j in range(max(lon0 - _MAX_CELL_RADIUS, 0), min(lon0 + _MAX_CELL_RADIUS + 1, len(lon_vals))):
            if bool(da.isel(latitude=i, longitude=j).isnull().all()):
                continue
            dist2 = (lat_vals[i] - lat) ** 2 + (lon_vals[j] - lon) ** 2
            if best is None or dist2 < best[0]:
                best = (dist2, i, j)

    if best is None:
        raise ValueError(
            f"no valid {da.name} cell within {_MAX_CELL_RADIUS} grid cells of ({lat}, {lon})"
        )
    return best[1], best[2]


def _extract_point(ds: xr.Dataset, lat: float, lon: float) -> pd.DataFrame:
    """Nearest-valid-cell extraction (land-masked cells skipped) + 3h -> 1h interpolation."""
    da = ds[_VARIABLE]
    i, j = _nearest_valid_cell(da, lat, lon)
    point = da.isel(latitude=i, longitude=j)
    df = point.to_dataframe(name="hs_baseline")[["hs_baseline"]]
    df.index = pd.to_datetime(df.index, utc=True)
    df.index.name = "time"
    df = df.sort_index()
    df = df.resample("1h").interpolate()
    return df


def fetch_wave_forecast(
    stations: list[Station], run_date: date, lookback_days: int = 0, horizon_days: int = 2
) -> dict[str, pd.DataFrame]:
    """One CMEMS subset for every wave station's baseline.

    `lookback_days`/`horizon_days` widen the window around `run_date` without
    adding a request: the daily run needs `[t0-24h, t0+48h]` for feature
    building (recent-error features + the published horizon), wider than the
    plain `[run_date, run_date+2d)` used by training's fixed-hour replay.
    """
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
                start_datetime=(run_date - timedelta(days=lookback_days)).isoformat(),
                end_datetime=(run_date + timedelta(days=horizon_days)).isoformat(),
                output_directory=tmpdir,
                output_filename="mfwam_subset.nc",
            )
            nc_path = Path(response.output_directory) / response.file_path
            ds = xr.open_dataset(nc_path).load()
        result = {s.id: _extract_point(ds, s.lat, s.lon) for s in wave_stations}
    except Exception as exc:
        raise SourceError("mfwam", f"mfwam subset failed: {exc}") from exc

    return result
