#!/usr/bin/env python
"""Build the per-station training datasets -> pipeline/data_train/<station>.parquet.

Run:  cd pipeline && uv run python scripts/build_dataset.py [--days 1825]

Sources and documented compromises
----------------------------------
* Waves: Candhis observations come from its historical API; Météo-France buoy
  pilots read the committed `data_obs_archive/` corpus populated by the single
  upstream `archive-obs` collection. The builder never re-queries `/bouees`,
  and a pilot is considered only with the explicit `--include-pilots` flag.
* Wave models (Open-Meteo Marine, `sources.marine`): ONE request per station
  for the 5 candidate wave models, raw Hs, no baseline selection here — that
  choice (and feature assembly) moves to `train.py` (Task 5), because it must
  happen after the per-station baseline pick. `--kind wave` therefore writes
  one raw parquet per station (`<station>_raw.parquet`: obs + 5 wave models +
  6 multi-model wind columns) instead of an assembled (X, y) dataset.
* Atmospheric forcing (Open-Meteo): ONE request per station over the whole
  window, hourly 10 m wind converted to u/v (+ the MSL pressure anomaly on
  `tide`). Every kind trains on **past forecasts of the model it will be
  served**, never on a reanalysis — `fetch_wind_models_history` (Historical
  Forecast API) for wave/wind, `fetch_tide_forcing_history` (Previous Runs API,
  ECMWF) for tide. The tide leg went ERA5 -> past ARPEGE -> stratified ECMWF on
  2026-08-04: pressure had become a dominant feature, and a forcing concatenated
  from the freshest runs turned the +48 h figures into a measurement of hindcast
  skill. Each intermediate leg was deleted rather than kept for `backfill.py`: a
  replayed day is flagged `backfilled`, but it is still displayed and scored, so
  leaving it on a different forcing would put the skew back on the serve side.
  Cost of the switch: tide rows cannot start before `TIDE_FORCING_START`
  (2024-02-05), which is now what bounds a tide dataset — not the observations.
* Tide (REFMAR): raw high-frequency observations, chunked in 30-day requests
  (API caps a request at 31 days). Real archive depth is discovered at runtime.
* Tide baseline (harmonic): **causal rolling fit** (`harmonic.causal_predict`).
  A first model is fitted on the first `harmonic.FIT_LOOKBACK_DAYS` of the
  station's own observations, then refitted every `--refit-days` on a *sliding*
  `harmonic.FIT_LOOKBACK_DAYS` window (two years). The model serving a given valid
  time is always fitted on observations strictly anterior to the simulated issue
  — no leak — on the same span *and à la même cadence* que ce que la production
  sert (`harmonic.FIT_LOOKBACK_DAYS`, `harmonic.REFIT_DAYS`), ce qui est ce qui
  rend le backtest honnête. Ne surtout pas raccourcir `--refit-days` pour
  « améliorer » la baseline : personne ne sert celle-là.
  Hence the default `--days 1825`: `FIT_LOOKBACK_DAYS` (two years) to fit before
  the first evaluated day, then three years to evaluate — enough for `train.py`
  to hold out a **full year** of test and still keep two years of training, so a
  tide station's verdict stops depending on the season it was retrained in.
  Shorten it and the first fits fall back to less history than production serves;
  the backtest then scores a baseline worse than the real one, which is the same
  skew in the other direction.
  Un fit figé avait autrefois rendu la baseline malhonnêtement mauvaise (offset
  de ~-0,3 m à 6 mois) : c'était utide extrapolant sa tendance séculaire, ce que
  `harmonic.fit` ne fait plus (`trend=False`). Ce qui reste vrai, c'est qu'un
  fit *jamais* rafraîchi dérive — d'où une cadence, mesurée, plutôt qu'un gel.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from scoreboard import archive, harmonic
from scoreboard.config import Station, load_env, load_stations
from scoreboard.dataset import HORIZON_H, assemble
from scoreboard.sources.candhis import fetch_wave_obs
from scoreboard.sources.mfbuoy import read_archived_buoy_obs
from scoreboard.sources.marine import fetch_wave_models_history
from scoreboard.sources.mfobs import fetch_wind_obs_archive
from scoreboard.sources.waterlevel import fetch_tide_obs
from scoreboard.sources.wind import (
    TIDE_FORCING_START,
    WIND_MODELS_START,
    fetch_tide_forcing_history,
    fetch_wind_models_history,
)

OUT_DIR = Path(__file__).resolve().parents[1] / "data_train"

# Above this, obs stopping short of the window's end is not a station going
# quiet for a day — it is a truncated archive (e.g. a source capping a
# response well before "now" while the raw frame still spans to "now" via the
# model columns). Fail loud rather than write a dataset silently missing its
# most recent months.
_MAX_OBS_GAP = timedelta(days=7)


def _build_raw(
    stations: list[Station],
    obs_column: str,
    fetch: Callable[[Station], pd.DataFrame],
) -> dict[str, pd.DataFrame]:
    """Write one `<id>_raw.parquet` per station and report its obs coverage.

    Seul chemin d'écriture de `data_train/` pour les kinds à modèles : un kind de
    plus fournit son `fetch` et sa colonne d'obs, pas une deuxième boucle à garder
    en phase. `train._model_data` relit les deux de la même façon — la convention
    de nommage et le comptage de couverture doivent donc être écrits une fois.
    """
    out = {}
    for st in stations:
        raw = fetch(st)
        obs_valid = raw[obs_column].dropna()
        if obs_valid.empty:
            raise RuntimeError(f"{st.id}: aucune observation exploitable dans la fenêtre demandée")
        gap = raw.index[-1] - obs_valid.index[-1]
        if gap > _MAX_OBS_GAP:
            raise RuntimeError(
                f"{st.id}: obs arrêtées le {obs_valid.index[-1]:%Y-%m-%d}, "
                f"{gap} avant la fin de fenêtre ({raw.index[-1]:%Y-%m-%d}) — "
                "archive tronquée, dataset non écrit"
            )
        raw.to_parquet(OUT_DIR / f"{st.id}_raw.parquet")
        out[st.id] = raw
        print(f"  {st.id}: {len(raw)}h, obs {raw[obs_column].notna().mean():.0%} couverts")
    return out


def build_wave(
    stations: list[Station],
    start: date,
    end: date,
    *,
    obs_archive_dir: Path | None = None,
) -> dict[str, pd.DataFrame]:
    """One raw parquet per station: obs + 5 wave models + 6 multi-model wind
    columns, hourly UTC. Candhis is fetched from its historical API; `mfbuoy`
    is read from the archive collected once upstream. No feature assembly here
    (Task 5/train.py)."""

    obs_archive_dir = obs_archive_dir or archive.DEFAULT_OBS_ARCHIVE_DIR

    def fetch(st: Station) -> pd.DataFrame:
        if st.source == "candhis":
            obs = fetch_wave_obs(st, start)[["hs"]].resample("1h").mean()
        elif st.source == "mfbuoy":
            obs = (
                read_archived_buoy_obs(obs_archive_dir, st.source_id, start, end)[["hs"]]
                .resample("1h")
                .mean()
            )
        else:
            raise ValueError(f"{st.id}: unsupported wave source {st.source!r}")
        waves = fetch_wave_models_history(st, start, end)  # single request
        winds = fetch_wind_models_history(st, start, end)  # single request
        return obs.join(waves, how="outer").join(winds, how="outer")

    return _build_raw(stations, "hs", fetch)


def build_wind(stations: list[Station], start: date, end: date) -> dict[str, pd.DataFrame]:
    """One raw parquet per station: obs FF + 3 model wind speeds + their u/v, hourly UTC.

    Deux sources seulement, et **une seule** requête Open-Meteo
    (`with_speeds=True` : baseline et forçage sortent du même payload).
    """

    def fetch(st: Station) -> pd.DataFrame:
        obs = fetch_wind_obs_archive(st, start, end)[["wind_speed"]]
        models = fetch_wind_models_history(st, start, end, with_speeds=True)
        return obs.join(models, how="outer")

    return _build_raw(stations, "wind_speed", fetch)


def build_tide(
    stations: list[Station], start: date, end: date, refit_days: int
) -> dict[str, tuple]:
    out = {}
    for st in stations:
        obs = fetch_tide_obs(st, start, date_end=end)
        level = obs["level"].dropna()
        if not harmonic.enough_for_fit(level):
            print(
                f"  {st.id}: only {len(level)}h of obs — too short to fit a tide", file=sys.stderr
            )
            out[st.id] = (pd.DataFrame(), pd.Series(dtype=float))
            continue

        # The first cutoff is exactly one full fit window in, never a fraction of
        # the record: the fit depth is a fixed constant now, so a `--fit-frac`
        # knob could only make the first fits shallower than production's — and
        # every day between `FIT_LOOKBACK_DAYS` and that fraction would be
        # evaluable data thrown away for nothing.
        # Never earlier than the forcing archive: an issue with no forcing is
        # dropped by `features.py` anyway, so fitting a baseline for it would be
        # utide runs spent on rows that cannot exist.
        split = max(
            level.index[0] + pd.Timedelta(days=harmonic.FIT_LOOKBACK_DAYS),
            pd.Timestamp(TIDE_FORCING_START, tz="UTC"),
        )
        baseline_s = harmonic.causal_predict(
            level,
            st.lat,
            obs.index,
            first_cutoff=split,
            refit_days=refit_days,
            horizon_hours=HORIZON_H,
        )
        eval_obs = obs.loc[baseline_s.index]
        # Past ECMWF runs stratified by age: same model production serves, and
        # a +48 h row is forced by a run that really was 2 days old.
        forcing = fetch_tide_forcing_history(st, max(start, TIDE_FORCING_START), end)
        out[st.id] = assemble(st, eval_obs, pd.DataFrame({"level_baseline": baseline_s}), forcing)
        print(
            f"  {st.id}: forcing {len(forcing)}h, obs {len(level)}h "
            f"({level.index[0]:%Y-%m-%d} -> {level.index[-1]:%Y-%m-%d}), "
            f"harmonic refit every {refit_days}d from {split:%Y-%m-%d}"
        )
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--days",
        type=int,
        default=1825,
        help="history depth to request (tide needs FIT_LOOKBACK_DAYS to fit + a year to eval)",
    )
    ap.add_argument(
        "--refit-days",
        type=int,
        default=harmonic.REFIT_DAYS,
        help="harmonic refit cadence (tide stations) — doit rester celle servie",
    )
    # Candhis has a daily quota: `--kind tide` reruns the tide half without re-fetching waves.
    ap.add_argument("--kind", choices=["wave", "tide", "wind"], help="build only this station kind")
    ap.add_argument(
        "--include-pilots",
        action="store_true",
        help="include inactive pilot stations (never enabled implicitly)",
    )
    ap.add_argument(
        "--station",
        default="",
        metavar="IDS",
        help="comma-separated station ids to build (default: all eligible stations)",
    )
    args = ap.parse_args()
    load_env()

    end = date.today()
    start = end - timedelta(days=args.days)
    selectable = load_stations(include_inactive=args.include_pilots)
    if only := {station_id.strip() for station_id in args.station.split(",") if station_id.strip()}:
        if unknown := only - {s.id for s in selectable}:
            ap.error(f"unknown or inactive station id(s) {sorted(unknown)}")
        selectable = [s for s in selectable if s.id in only]
    stations = [s for s in selectable if args.kind in (None, s.kind)]
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    print(f"Window {start} -> {end}")
    datasets: dict[str, tuple] = {}
    waves = [s for s in stations if s.kind == "wave"]
    winds = [s for s in stations if s.kind == "wind"]
    tides = [s for s in stations if s.kind == "tide"]
    if waves:
        print("Waves (source obs configured + Open-Meteo multi-model), raw parquet per station:")
        build_wave(waves, start, end)  # writes its own <station>_raw.parquet
    if winds:
        # Départ borné par la dispo réelle des 3 modèles, pas par `--days` : voir
        # `WIND_MODELS_START`. `--days` peut raccourcir la fenêtre, jamais l'étendre.
        wind_start = max(start, WIND_MODELS_START)
        print(f"Wind (Météo-France DPClim + Open-Meteo multi-model) from {wind_start}:")
        build_wind(winds, wind_start, end)  # writes its own <station>_raw.parquet
    if tides:
        print("Tide (REFMAR + harmonic):")
        datasets |= build_tide(tides, start, end, args.refit_days)

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
