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
* Wind (Open-Meteo / ERA5): ONE archive request per station over the whole
  window, hourly 10 m wind converted to u/v. **Documented train/serve skew**:
  training uses the ERA5 *reanalysis*, while the daily run will use the ARPEGE
  *forecast* (`sources.wind.fetch_wind_forecast`). Same category of compromise
  as the MFWAM analysis-as-forecast proxy above — a mean-bias-type skew, not an
  equivalence — and it resorbs the same way, by accumulating real forecast runs.
* Tide (REFMAR): raw high-frequency observations, chunked in 30-day requests
  (API caps a request at 31 days). Real archive depth is discovered at runtime.
* Tide baseline (harmonic): **causal rolling fit** (`harmonic.causal_predict`).
  A first model is fitted on the oldest `--fit-frac` of the station's own
  observations, then refitted every `--refit-days` on the expanding history.
  The model serving a given valid time is always fitted on observations strictly
  anterior to the simulated issue — no leak, and it is what production will do
  (Task 8 refits periodically on the whole archive). A single frozen fit made the
  baseline dishonestly bad: utide extrapolates its secular trend, so a 6-month-old
  fit carried a ~-0.3 m offset that the model was rewarded for merely removing.
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
from scoreboard.dataset import HORIZON_H, assemble
from scoreboard.sources.candhis import fetch_wave_obs
from scoreboard.sources.mfwam import _DATASET_ID, _VARIABLE, _extract_point
from scoreboard.sources.waterlevel import fetch_tide_obs
from scoreboard.sources.wind import fetch_wind_history

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
        wind = fetch_wind_history(st, start, end)  # single ERA5 request
        out[st.id] = assemble(st, obs, baselines[st.id], wind)
        print(f"  {st.id}: obs {len(obs)}h, baseline {len(baselines[st.id])}h, wind {len(wind)}h")
    return out


def build_tide(
    stations: list[Station], start: date, end: date, fit_frac: float, refit_days: int
) -> dict[str, tuple]:
    out = {}
    for st in stations:
        obs = fetch_tide_obs(st, start, date_end=end)
        level = obs["level"].dropna()
        if len(level) < 24 * 30:
            print(f"  {st.id}: only {len(level)}h of obs — too short to fit a tide", file=sys.stderr)
            out[st.id] = (pd.DataFrame(), pd.Series(dtype=float))
            continue

        split = level.index[int(len(level) * fit_frac)]
        baseline_s = harmonic.causal_predict(
            level, st.lat, obs.index, first_cutoff=split, refit_days=refit_days,
            horizon_hours=HORIZON_H,
        )
        eval_obs = obs.loc[baseline_s.index]
        wind = fetch_wind_history(st, start, end)  # single ERA5 request
        out[st.id] = assemble(st, eval_obs, pd.DataFrame({"level_baseline": baseline_s}), wind)
        print(
            f"  {st.id}: wind {len(wind)}h, obs {len(level)}h "
            f"({level.index[0]:%Y-%m-%d} -> {level.index[-1]:%Y-%m-%d}), "
            f"harmonic refit every {refit_days}d from {split:%Y-%m-%d}"
        )
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=365, help="history depth to request")
    ap.add_argument("--fit-frac", type=float, default=0.5, help="share of tide obs used to fit")
    ap.add_argument(
        "--refit-days", type=int, default=30, help="harmonic refit cadence (tide stations)"
    )
    # Candhis has a daily quota: `--kind tide` reruns the tide half without re-fetching waves.
    ap.add_argument("--kind", choices=["wave", "tide"], help="build only this station kind")
    args = ap.parse_args()

    end = date.today()
    start = end - timedelta(days=args.days)
    stations = [s for s in load_stations() if args.kind in (None, s.kind)]
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
        datasets |= build_tide(tides, start, end, args.fit_frac, args.refit_days)

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
