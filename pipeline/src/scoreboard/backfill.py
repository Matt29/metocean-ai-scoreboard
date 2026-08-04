"""Backfill: fill history gaps (a failed run, a source outage, or a cold start
with empty history) — one deep fetch per source for the *whole* window, then a
purely in-memory, offline replay of each missing day.

Résolution 1 (the point of this module): `daily.run` costs one Candhis request
per wave station, one Open-Meteo marine request per wave station, one Open-Meteo
forcing request per station, and (Task 8) a 90-day REFMAR fetch per tide station —
*per invocation*. A naive loop calling `daily.run` once per missing day would
multiply every one of those by the number of days replayed. Here, every source
is fetched exactly once for the entire `[since, yesterday]` window (REFMAR is
chunked into ~31-day requests by `fetch_tide_obs` itself — still one *call*,
not one per day), and each missing day is then computed against that
in-memory data — no further network I/O.

Résolution 2: a replayed day is served the *archived* Open-Meteo fields (the
historical-forecast leg for wind, the marine archive for waves) rather than the
live forecasts `daily.py` runs on — the same proxies `scripts/build_dataset.py`
trains on, reused here rather than re-implemented. Every day entry this module writes carries `"backfilled":
true` so the site can tell a reconstructed day from a genuinely live one.

Résolution 3: this module never touches `daily._score_previous_issue`'s
same-day guard — it does not go through `daily.run` at all. Each missing day
is instead issued and scored in one step, directly from the deep in-memory
obs (see `issue_series`/`score_series` in `daily.py`, the exact same compute
core `daily.run` uses) — so there is no `latest.json` round-trip for the
guard to apply to, or to bypass. For the same reason this module never calls
`publish.write_latest`: a past issue sitting in `latest.json` is exactly what
would have re-armed the guard's blind spot (`daily.run`'s next real run would
re-score an old day with only `OBS_LOOKBACK_DAYS` of obs, silently downgrading
or destroying an already-scored `"ok"`/`"backfilled"` day) — see Task 9's
review, blocker 2.

Résolution 5 (idempotence): a day already present in `history.json` **with
status "ok"** is never touched again — see `_missing_dates`. A `"missing"`
day is retried on every subsequent call: `upsert_history` replaces by date so
this cannot duplicate, the deep fetch is still exactly-once per call, and it
is the only recovery path from a transient source failure (a 429/503/400,
below) without hand-editing `history.json`.

Known limitation, not fixed here: `history.json` is capped at
`publish.MAX_HISTORY_DAYS` (90). A `--since` older than 90 days from today
will see its oldest replayed days evicted by `upsert_history` as soon as more
recent days push past the cap — the *next* `backfill --since` covering that
same old range will find them missing again and re-fetch/re-replay them. Not
a duplicate (still keyed by date, still capped at 90), but not the announced
no-op either. Out of scope: fixing it means keeping backfilled history beyond
the site's 90-day display window, a different (unrequested) feature.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from scoreboard import daily, publish
from scoreboard.config import Station, load_stations
from scoreboard.sources import SourceError
from scoreboard.sources.candhis import fetch_wave_obs
from scoreboard.sources.marine import fetch_wave_models_history
from scoreboard.sources.mfobs import fetch_wind_obs_archive
from scoreboard.sources.waterlevel import fetch_tide_obs
from scoreboard.sources.wind import (
    MULTI_FORCING_COLUMNS,
    WIND_MODEL_COLUMNS,
    fetch_tide_forcing_history,
    fetch_wind_models_history,
)

log = logging.getLogger(__name__)

_WIND_MARGIN_DAYS = 2  # small pre-roll so the first replayed day's -24h baseline lookback has forcing


def _missing_dates(out_dir: Path, station_id: str, since: date, until: date) -> list[date]:
    """Dates in `[since, until]` without an `"ok"` `history.json` entry —
    chronological. A `"missing"` day is deliberately NOT considered done: it is
    the only recovery path from a transient source failure (résolution 5)."""
    if since > until:
        return []
    history = publish.read_history(out_dir, station_id)
    ok = {d["date"] for d in (history["days"] if history else []) if d.get("status") == "ok"}
    n_days = (until - since).days + 1
    return [d for i in range(n_days) if (d := since + timedelta(days=i)).isoformat() not in ok]


def _deep_window(since: date, until: date, today: date, pre_roll_days: int) -> tuple[date, date]:
    """`(start, end)` for a source fetched once over the whole replay window.

    `pre_roll_days` before `since`, so the first replayed day's -24h baseline
    lookback is covered. `+3d` after `until`, so the last replayed day's +48h
    horizon has something to score against — but never past `today`: the
    REFMAR/Open-Meteo archive endpoints hard-reject a future `end_date` with an
    HTTP 400, verified against the live endpoint (Task 9 review, blocker 1).
    One definition, so the clamp can never drift between two of the sources.
    """
    return since - timedelta(days=pre_roll_days), min(until + timedelta(days=3), today)


def _deep_obs(station: Station, since: date, until: date, today: date) -> pd.Series:
    """One fetch covering the whole backfill window (résolution 1) — same lookback
    depth `daily._fetch_obs` uses per-day, just anchored once at `since`. `date_end`
    is clamped to `today`: REFMAR/Open-Meteo reject a future end date outright (see
    `_deep_forcing`), and there is no archived observation to fetch beyond "now" anyway.

    Le dispatch porte sur `station.source`, pas sur `station.kind` : c'est la
    source qui détermine à qui on parle. Voir `daily._fetch_obs`, même règle.
    """
    if station.source == "candhis":
        start = since - timedelta(days=daily.OBS_LOOKBACK_DAYS)
        df = fetch_wave_obs(station, start)  # candhis has no end param: serves up to "now"
        return df["hs"].astype(float).dropna().sort_index()
    if station.source == "mfobs":
        # DPClim, pas DPObs : le temps réel ne garde qu'une fenêtre glissante de
        # quelques jours, donc lui seul ne peut pas rejouer un jour ancien.
        start, date_end = _deep_window(since, until, today, daily.OBS_LOOKBACK_DAYS)
        df = fetch_wind_obs_archive(station, start, date_end)
        return df["wind_speed"].astype(float).dropna().sort_index()
    if station.source == "shom":
        start, date_end = _deep_window(since, until, today, daily.TIDE_FIT_LOOKBACK_DAYS)
        df = fetch_tide_obs(station, start, date_end=date_end)
        return df["level"].astype(float).dropna().sort_index()
    raise SourceError(station.id, f"aucun collecteur d'obs pour la source {station.source!r}")


def _deep_inputs(
    station: Station, since: date, until: date, today: date
) -> tuple[pd.DataFrame | None, pd.DataFrame]:
    """`(models, forcing)` over the whole replay window — the one place the
    wave, wind and tide paths pick different sources, mirroring `daily._fetch_inputs`.

    Archived fields, not live forecasts (résolution 2): the 5 wave models plus
    the 3 candidate winds for a wave station, the run-stratified ECMWF leg for a
    tide station — `daily.issue_series` narrows it per replayed issue, so a
    backfilled day is forced by the run it would really have had. Whatever of the last replayed day's +48h horizon falls beyond
    `_deep_window`'s clamp is simply unavailable yet; `features.py`'s
    forcing-coverage floor then marks that day `"missing"` rather than publish a
    degraded correction — and because `_missing_dates` only treats `"ok"` as
    done, a later backfill call retries it once the archive catches up (no
    permanent poisoning, résolution 3 in the review).
    """
    start, date_end = _deep_window(since, until, today, _WIND_MARGIN_DAYS)
    if station.kind == "wave":
        return (
            fetch_wave_models_history(station, start, date_end),
            fetch_wind_models_history(station, start, date_end),
        )
    if station.kind == "wind":
        # Une seule requête : vitesses (baseline) et u/v (forçage) sortent du même
        # payload — même économie que `daily._fetch_inputs`.
        frame = fetch_wind_models_history(station, start, date_end, with_speeds=True)
        return frame[WIND_MODEL_COLUMNS], frame[MULTI_FORCING_COLUMNS]
    return None, fetch_tide_forcing_history(station, start, date_end)


def _backfill_station(
    station: Station,
    since: date,
    until: date,
    today: date,
    out_dir: Path,
    models_dir: Path | None,
    missing: list[date],
) -> list[str]:
    if not missing:
        return []

    try:
        obs = _deep_obs(station, since, until, today)
        model_frame, forcing = _deep_inputs(station, since, until, today)
    except Exception as exc:  # noqa: BLE001 - a deep-fetch failure must not abort other stations
        log.warning("%s: backfill deep-fetch failed: %s", station.id, exc)
        for d in missing:
            publish.upsert_history(
                out_dir, station.id, {"date": d.isoformat(), "status": "missing", "backfilled": True}
            )
        return [d.isoformat() for d in missing]

    replayed: list[str] = []
    # Chronological, for determinism/readability only (résolution 6): each day
    # below is issued and scored independently against the same deep in-memory
    # obs, so day N never consumes day N-1's output — there is no latest.json
    # write here for the order to matter to (see the module docstring, blocker
    # 2: a past issue must never overwrite the live latest.json).
    for d in missing:
        t0 = pd.Timestamp(d, tz="UTC") + pd.Timedelta(hours=daily.ISSUE_HOUR)
        try:
            series, baseline_model = daily.issue_series(
                station, obs, t0, model_frame, forcing, models_dir
            )
        except Exception as exc:  # noqa: BLE001 - degenerate baseline, missing model, bad forcing
            log.warning("%s: backfill issue failed for %s: %s", station.id, d, exc)
            entry = {"date": d.isoformat(), "status": "missing"}
        else:
            # Score immediately against the deep obs already in memory — no
            # latest.json round-trip, no second scoring code path.
            entry = daily.score_series(obs, series, t0, baseline_model)
        entry["backfilled"] = True
        publish.upsert_history(out_dir, station.id, entry)
        replayed.append(d.isoformat())

    # Same sweep as daily's `_run_station`: `score_series` (above and in past
    # runs) leaves 25-48h leads "pending" until their obs exist — the deep obs
    # already in memory are the richest this station will ever see, so complete
    # (or age out) whatever is still pending while we hold them. Without this,
    # a station driven only by backfill would carry orphaned pending forever.
    daily._rescore_pending(station, obs, out_dir, today)
    return replayed


def run(
    since: date,
    out_dir: Path,
    *,
    today: date | None = None,
    stations: list[Station] | None = None,
    gate: dict | None = None,
    models_dir: Path | None = None,
) -> dict[str, list[str]]:
    """Replay every missing day in `[since, yesterday]` for every gate-passing
    station. Returns `{station_id: [replayed dates, chronological]}`."""
    stations = stations if stations is not None else load_stations()
    gate = gate if gate is not None else daily.load_gate()
    today = today or datetime.now(timezone.utc).date()
    until = today - timedelta(days=1)

    # Same as daily.run: stations.json must exist even if backfill is the very
    # first thing ever run against an empty data/ dir (cold start, minor #1).
    publish.write_stations(out_dir, stations, gate)

    published = [s for s in stations if gate.get(s.id, {}).get("pass", False)]

    # Missing dates first, so a station with nothing to replay never fetches
    # anything at all (résolution 1 applies even at zero days).
    missing_by_station = {
        st.id: _missing_dates(out_dir, st.id, since, until) for st in published
    }

    summary: dict[str, list[str]] = {}
    for st in published:
        try:
            summary[st.id] = _backfill_station(
                st, since, until, today, out_dir, models_dir, missing_by_station[st.id]
            )
        except Exception as exc:  # noqa: BLE001 - one station's failure must never be global
            log.warning("%s: backfill failed: %s", st.id, exc)
            summary[st.id] = []

    # Skip on a strict no-op (minor #2): recomputing scores.json's "updated"
    # timestamp when nothing changed would be a visible side effect for zero work.
    if any(summary.values()):
        publish.write_scores(
            out_dir, [s.id for s in published], daily.iso(pd.Timestamp(datetime.now(timezone.utc)))
        )
    return summary
