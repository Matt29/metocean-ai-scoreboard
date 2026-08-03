#!/usr/bin/env python
"""Build the per-station training datasets -> pipeline/data_train/<station>.parquet.

Run:  cd pipeline && uv run python scripts/build_dataset.py [--days 365]

Sources and documented compromises
----------------------------------
* Waves (Candhis): ONE `getCampTR.php` call per station covering the whole
  window (the TR archive serves >= 365 days in a single request — verified in
  the Task-1 spike, docs/data-sources.md). Candhis has a daily quota, so this
  script deliberately never loops per-day.
* Wave models (Open-Meteo Marine, `sources.marine`): ONE request per station
  for the 5 candidate wave models, raw Hs, no baseline selection here — that
  choice (and feature assembly) moves to `train.py` (Task 5), because it must
  happen after the per-station baseline pick. `--kind wave` therefore writes
  one raw parquet per station (`<station>_raw.parquet`: obs + 5 wave models +
  6 multi-model wind columns) instead of an assembled (X, y) dataset.
* Atmospheric forcing (Open-Meteo / ERA5): ONE archive request per station over
  the whole window, hourly 10 m wind converted to u/v. **Documented train/serve
  skew**: training uses the ERA5 *reanalysis*, while the daily run will use ARPEGE
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
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from scoreboard import harmonic
from scoreboard.config import Station, load_env, load_stations
from scoreboard.dataset import HORIZON_H, assemble
from scoreboard.sources.candhis import fetch_wave_obs
from scoreboard.sources.marine import fetch_wave_models_history
from scoreboard.sources.waterlevel import fetch_tide_obs
from scoreboard.sources.wind import fetch_wind_history, fetch_wind_models_history

OUT_DIR = Path(__file__).resolve().parents[1] / "data_train"


def build_wave(stations: list[Station], start: date, end: date) -> dict[str, pd.DataFrame]:
    """One raw parquet per station: obs + 5 wave models + 6 multi-model wind
    columns, hourly UTC. No feature assembly here (Task 5/train.py)."""
    out = {}
    for st in stations:
        obs = fetch_wave_obs(st, start)[["hs"]].resample("1h").mean()  # single deep request
        waves = fetch_wave_models_history(st, start, end)  # single request
        winds = fetch_wind_models_history(st, start, end)  # single request
        raw = obs.join(waves, how="outer").join(winds, how="outer")
        raw.to_parquet(OUT_DIR / f"{st.id}_raw.parquet")
        out[st.id] = raw
        print(f"  {st.id}: {len(raw)}h, obs {raw['hs'].notna().mean():.0%} couverts")
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
        forcing = fetch_wind_history(st, start, end)  # single ERA5 request
        out[st.id] = assemble(st, eval_obs, pd.DataFrame({"level_baseline": baseline_s}), forcing)
        print(
            f"  {st.id}: forcing {len(forcing)}h, obs {len(level)}h "
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
    load_env()

    end = date.today()
    start = end - timedelta(days=args.days)
    stations = [s for s in load_stations() if args.kind in (None, s.kind)]
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    print(f"Window {start} -> {end}")
    datasets: dict[str, tuple] = {}
    waves = [s for s in stations if s.kind == "wave"]
    tides = [s for s in stations if s.kind == "tide"]
    if waves:
        print("Waves (Candhis + Open-Meteo multi-model), raw parquet per station:")
        build_wave(waves, start, end)  # writes its own <station>_raw.parquet
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
