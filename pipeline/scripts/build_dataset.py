#!/usr/bin/env python
"""Build the per-station training datasets -> pipeline/data_train/<station>.parquet.

Run:  cd pipeline && uv run python scripts/build_dataset.py [--days 365]

Sources and documented compromises
----------------------------------
* Waves (Candhis): ONE `getCampTR.php` call per station covering the whole
  window (the TR archive serves >= 365 days in a single request — verified in
  the Task-1 spike, docs/data-sources.md). Candhis has a daily quota, so this
  script deliberately never loops per-day.
* Wave baseline (MFWAM): the *analysis* fields of the same `anfc` dataset used
  for the live forecast, taken as a proxy for the archived forecast. Analysis
  is closer to truth than a real +24h forecast, so the training set is slightly
  optimistic. Accepted for v1 (there is no free archive of past MFWAM runs);
  the public scoreboard is scored on real forecasts, not on this proxy.
* Tide (REFMAR): raw high-frequency observations, chunked in 30-day requests
  (API caps a request at 31 days). Real archive depth is discovered at runtime.
* Tide baseline (harmonic): fitted on the OLDEST `--fit-frac` of the station's
  own observations; the dataset is assembled only on the remaining, later
  window. The harmonic constants therefore never saw the evaluated hours.
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from scoreboard import harmonic
from scoreboard.config import Station, load_stations
from scoreboard.dataset import assemble
from scoreboard.sources.candhis import fetch_wave_obs
from scoreboard.sources.mfwam import _DATASET_ID, _VARIABLE, _extract_point
from scoreboard.sources.waterlevel import fetch_tide_obs

OUT_DIR = Path(__file__).resolve().parents[1] / "data_train"
BBOX_MARGIN = 0.2


def fetch_mfwam_history(stations: list[Station], start: date, end: date) -> dict[str, pd.DataFrame]:
    """One CMEMS subset for the whole bbox/period, then point extraction per station."""
    import copernicusmarine
    import xarray as xr

    lats = [s.lat for s in stations]
    lons = [s.lon for s in stations]
    with tempfile.TemporaryDirectory() as tmpdir:
        response = copernicusmarine.subset(
            dataset_id=_DATASET_ID,
            variables=[_VARIABLE],
            minimum_longitude=min(lons) - BBOX_MARGIN,
            maximum_longitude=max(lons) + BBOX_MARGIN,
            minimum_latitude=min(lats) - BBOX_MARGIN,
            maximum_latitude=max(lats) + BBOX_MARGIN,
            start_datetime=start.isoformat(),
            end_datetime=end.isoformat(),
            output_directory=tmpdir,
            output_filename="mfwam_history.nc",
        )
        ds = xr.open_dataset(Path(response.output_directory) / response.file_path).load()
    return {s.id: _extract_point(ds, s.lat, s.lon) for s in stations}


def build_wave(stations: list[Station], start: date, end: date) -> dict[str, tuple]:
    baselines = fetch_mfwam_history(stations, start, end)
    out = {}
    for st in stations:
        obs = fetch_wave_obs(st, start)  # single deep request (quota-friendly)
        obs = obs[["hs"]].resample("1h").mean()  # 30-min native -> hourly
        out[st.id] = assemble(st, obs, baselines[st.id])
        print(f"  {st.id}: obs {len(obs)}h, baseline {len(baselines[st.id])}h")
    return out


def build_tide(stations: list[Station], start: date, end: date, fit_frac: float) -> dict[str, tuple]:
    out = {}
    for st in stations:
        obs = fetch_tide_obs(st, start, date_end=end)
        level = obs["level"].dropna()
        if len(level) < 24 * 30:
            print(f"  {st.id}: only {len(level)}h of obs — too short to fit a tide", file=sys.stderr)
            out[st.id] = (pd.DataFrame(), pd.Series(dtype=float))
            continue

        split = level.index[int(len(level) * fit_frac)]
        model = harmonic.fit(level[level.index <= split], st.lat)
        eval_obs = obs[obs.index > split]
        baseline = pd.DataFrame({"level_baseline": model.predict(eval_obs.index)})
        out[st.id] = assemble(st, eval_obs, baseline)
        print(
            f"  {st.id}: obs {len(level)}h "
            f"({level.index[0]:%Y-%m-%d} -> {level.index[-1]:%Y-%m-%d}), "
            f"harmonic fitted on {split:%Y-%m-%d} and before"
        )
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=365, help="history depth to request")
    ap.add_argument("--fit-frac", type=float, default=0.5, help="share of tide obs used to fit")
    args = ap.parse_args()

    end = date.today()
    start = end - timedelta(days=args.days)
    stations = load_stations()
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    print(f"Window {start} -> {end}")
    datasets: dict[str, tuple] = {}
    waves = [s for s in stations if s.kind == "wave"]
    tides = [s for s in stations if s.kind == "tide"]
    if waves:
        print("Waves (Candhis + MFWAM analysis):")
        datasets |= build_wave(waves, start, end)
    if tides:
        print("Tide (REFMAR + harmonic):")
        datasets |= build_tide(tides, start, end, args.fit_frac)

    print("\nrows written:")
    for station_id, (x, y) in datasets.items():
        if x.empty:
            print(f"  {station_id}: EMPTY — nothing written")
            continue
        df = x.copy()
        df["y"] = y.values
        df.to_parquet(OUT_DIR / f"{station_id}.parquet")
        print(f"  {station_id}: {len(df)} rows, {df.index[0]:%Y-%m-%d} -> {df.index[-1]:%Y-%m-%d}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
