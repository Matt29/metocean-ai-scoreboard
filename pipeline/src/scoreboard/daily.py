"""Daily orchestration: predict, score yesterday, publish — one run per calendar day.

For each station whose gate verdict is `pass: true` (résolution 2 — a station
that loses to its own baseline is never published):

1. Fetch the station's observations (one request, see `sources.candhis` /
   `sources.waterlevel` quotas) and use them to score the predictions this
   station published *yesterday* (read back from its own `latest.json`,
   matched to observations by nearest hour) — the scored result becomes a new
   `history.json` day entry.
2. Build today's baseline: MFWAM (one Copernicus call for every `wave`
   station, résolution 5) or a harmonic fit refitted on today's full
   observation history (résolution 4 — a stale fit is exactly the bug this
   project already paid for once, see `docs/data-sources.md`).
3. Fetch the ARPEGE wind forecast and run inference through the trained model.
4. Publish today's `latest.json`.
5. Archive the served wind forecast (`archive.write_day`, Task A1) for every
   station that reached step 4 — the corpus a future retrain needs to remove
   the ERA5-train/ARPEGE-serve skew documented in `docs/data-sources.md`. A
   failure here is logged, never allowed to undo the publish above.

Each station is wrapped in its own try/except (résolution 5): *any* exception
anywhere in a station's pipeline — obs, scoring, baseline, forcing, or model —
marks that station `"missing"` for the day and never reaches or blocks the
others. A history day entry's `"date"` means one of two distinct things,
both documented at the call site: the day a *previous* issue is finally
scored (keyed by *that issue's own* `issued` date, however long ago it was
issued) versus the day *this run* failed to issue anything at all (keyed by
`run_date`) — a single run can write both in the same call.

Two lookback windows, deliberately different since v1.1 (see the Task 8
review that caught a silent bug here): `OBS_LOOKBACK_DAYS` is a short window
covering the feature engineering needs (`last_err`/`mean_err_24h`) and the
scoring of the previous issue. `TIDE_FIT_LOOKBACK_DAYS` is the *harmonic fit*
window — utide needs enough history to separate the main tidal constituents
(M2/S2 need 14.8 days apart, M2/N2 need 27.6 days), so a fit on only a few
days silently returns nonsense amplitudes rather than raising. Below
`MIN_TIDE_FIT_DAYS` (the same 30-day floor `scripts/build_dataset.py` already
enforces at training time) the station is marked missing instead of serving
a degenerate baseline.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from scoreboard import archive, harmonic, model, publish
from scoreboard.config import Station, load_stations
from scoreboard.features import build_features
from scoreboard.sources import SourceError
from scoreboard.sources.candhis import fetch_wave_obs
from scoreboard.sources.mfwam import fetch_wave_forecast
from scoreboard.sources.waterlevel import fetch_tide_obs
from scoreboard.sources.wind import FORECAST_MODEL, fetch_wind_forecast

log = logging.getLogger(__name__)

ISSUE_HOUR = 6  # UTC, matches dataset.assemble's training default
OBS_LOOKBACK_DAYS = 4  # wave: >= 24h (mean_err_24h) + margin for a short outage
TIDE_FIT_LOOKBACK_DAYS = 90  # utide needs months, not days, to separate constituents
MIN_TIDE_FIT_DAYS = 30  # same hard floor as scripts/build_dataset.py's `24 * 30`
BASELINE_LOOKBACK_H = 24
BASELINE_HORIZON_H = 48
# A day scored the morning after its issue only meets ~24h of its own leads —
# the 25-48h tail stays "pending" in its history entry and is completed by
# `_rescore_pending` on later runs, as obs catch up. Beyond this age, every
# lead of the issue (<= +48h = date+2d) predates the daily obs window
# (`run_date - OBS_LOOKBACK_DAYS`), so no daily run can ever match it again:
# drop the dead weight instead of carrying it forever.
PENDING_MAX_AGE_DAYS = OBS_LOOKBACK_DAYS + 2
GATE_PATH = model.MODELS_DIR / "gate.json"


def load_gate(path: Path | None = None) -> dict:
    path = path or GATE_PATH
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def iso(t: pd.Timestamp) -> str:
    return t.isoformat().replace("+00:00", "Z")


def _fetch_obs(station: Station, run_date: date) -> pd.Series:
    """One station-level fetch (résolution 5). Tide requests `TIDE_FIT_LOOKBACK_DAYS`
    (chunked internally by `fetch_tide_obs`, ~3 HTTP requests — still one call here
    and well within quota) so the harmonic fit below never starves for history."""
    if station.kind == "wave":
        start = run_date - timedelta(days=OBS_LOOKBACK_DAYS)
        df = fetch_wave_obs(station, start)
        return df["hs"].astype(float).dropna().sort_index()
    start = run_date - timedelta(days=TIDE_FIT_LOOKBACK_DAYS)
    df = fetch_tide_obs(station, start, date_end=run_date + timedelta(days=1))
    return df["level"].astype(float).dropna().sort_index()


def _baseline_window(station: Station, obs: pd.Series, t0: pd.Timestamp, mfwam: dict) -> pd.Series:
    """`[t0-24h, t0+48h]` baseline series — MFWAM lookup or a fresh harmonic fit."""
    lo = t0 - pd.Timedelta(hours=BASELINE_LOOKBACK_H)
    hi = t0 + pd.Timedelta(hours=BASELINE_HORIZON_H)
    if station.kind == "wave":
        baseline = mfwam.get(station.id)
        if baseline is None or baseline.empty:
            raise SourceError(station.id, "no mfwam baseline available")
        # The Copernicus subset is fetched with a wider margin (lookback_days/
        # horizon_days in `run`) than the model was trained on; clip back to
        # the trained horizon so `lead_h` never extrapolates past 48h.
        return baseline["hs_baseline"][(baseline.index > lo) & (baseline.index <= hi)]

    # Tide: refit daily on every observation available up to t0. One
    # utide.solve call per tide station per run is negligible — unlike
    # training's 30-day-cadence backtest (needs ~180 fits/station to replay a
    # year causally), production runs exactly once a day, so "refit every
    # run" costs the same as "refit every 30 days" would, but never serves a
    # fit older than today's obs (résolution 4).
    past = obs[obs.index <= t0].dropna()
    min_hours = MIN_TIDE_FIT_DAYS * 24
    if len(past) < min_hours:
        raise SourceError(
            station.id,
            f"only {len(past)}h of tide obs (< {min_hours}h / {MIN_TIDE_FIT_DAYS}d) — "
            "refusing a degenerate harmonic fit",
        )
    fitted = harmonic.fit(past, station.lat)
    window = pd.date_range(
        t0 - pd.Timedelta(hours=BASELINE_LOOKBACK_H),
        t0 + pd.Timedelta(hours=BASELINE_HORIZON_H),
        freq="1h",
        tz="UTC",
    )
    return fitted.predict(window)


def score_series(obs: pd.Series, series: list[dict], issued_ts: pd.Timestamp) -> dict:
    """Pure scoring core (no I/O): match an issued `series` (`[{"t","ia","baseline"}]`)
    against `obs` (nearest hour, 1h tolerance) and build the `history.json` day entry.

    Shared by `_score_previous_issue` (reads `series` back off a `latest.json` written
    a day earlier) and `backfill.py` (scores a freshly regenerated `series` immediately,
    against the deep a-posteriori obs already held in memory — résolution 1, no second
    scoring code path).
    """
    day = issued_ts.date().isoformat()
    if not series:
        return {"date": day, "status": "missing"}

    times = pd.DatetimeIndex([pd.Timestamp(p["t"]) for p in series])
    ia = pd.Series([p["ia"] for p in series], index=times)
    baseline = pd.Series([p["baseline"] for p in series], index=times)
    matched = obs.reindex(times, method="nearest", tolerance=pd.Timedelta("1h"))
    keep = matched.notna()

    # Leads with no obs yet (typically 25-48h the morning after the issue) are
    # kept as "pending" so `_rescore_pending` can complete the day on a later
    # run instead of silently never verifying them.
    pending = [
        {"t": iso(t), "ia": round(float(ia[t]), 4), "baseline": round(float(baseline[t]), 4)}
        for t in times[~keep]
    ]

    if not keep.any():
        entry = {"date": day, "status": "missing"}
    else:
        mae_ia, mae_baseline = publish.score_day(matched[keep], ia[keep], baseline[keep])
        out_series = [
            {
                "t": iso(t),
                "obs": round(float(matched[t]), 4),
                "ia": round(float(ia[t]), 4),
                "baseline": round(float(baseline[t]), 4),
            }
            for t in times[keep]
        ]
        lead_hours = (times[keep] - issued_ts) / pd.Timedelta(hours=1)
        entry = {
            "date": day,
            "status": "ok",
            "series": out_series,
            "mae_ia": round(mae_ia, 4),
            "mae_baseline": round(mae_baseline, 4),
            "n_points": int(keep.sum()),
            # Ne couvre que les leads matchés par les obs disponibles au moment
            # du scoring (typiquement <= 24h en run quotidien — voir
            # `_fetch_obs`/`OBS_LOOKBACK_DAYS` — mais le plein horizon en
            # backfill, voir `backfill.py`). Les leads restants partent en
            # "pending" ci-dessous et sont complétés par `_rescore_pending`
            # quand leurs obs arrivent — `max_lead_h` monte alors vers 48.
            "max_lead_h": int(round(lead_hours.max())),
        }
    if pending:
        entry["pending"] = pending
    return entry


def rescore_entry(entry: dict, obs: pd.Series, *, drop_pending_before: date | None = None) -> dict:
    """Complete a partially scored day: match its `pending` leads against `obs`.

    Merge, never re-match: points already in `series` were scored against the
    obs available then and are kept verbatim — re-matching them against today's
    (shorter) obs window would silently *lose* matches, exactly the downgrade
    bug `backfill.py`'s module docstring documents (Task 9, blocker 2). Only
    the still-pending leads meet the fresh obs; the MAE/`n_points`/`max_lead_h`
    are then recomputed over the merged series (from the stored rounded values,
    so a rerun with no new match is a strict no-op — returns `entry` as-is,
    never a mutated copy).

    `drop_pending_before`: a day issued before this date has leads entirely
    older than any obs window a future run will fetch — whatever is still
    unmatched *after* the merge is dead weight and is dropped, so every caller
    (daily sweep, backfill sweep) gets the same aging rule for free.
    """
    pending = entry.get("pending") or []
    if not pending:
        return entry
    stale = drop_pending_before is not None and date.fromisoformat(entry["date"]) < drop_pending_before
    times = pd.DatetimeIndex([pd.Timestamp(p["t"]) for p in pending])
    matched = obs.reindex(times, method="nearest", tolerance=pd.Timedelta("1h"))
    keep = matched.notna()
    if not keep.any():
        if not stale:
            return entry
        return {k: v for k, v in entry.items() if k != "pending"}

    issued_ts = pd.Timestamp(entry["date"], tz="UTC") + pd.Timedelta(hours=ISSUE_HOUR)
    newly = [
        {"t": p["t"], "obs": round(float(matched[t]), 4), "ia": p["ia"], "baseline": p["baseline"]}
        for p, t, ok in zip(pending, times, keep)
        if ok
    ]
    series = sorted((entry.get("series") or []) + newly, key=lambda p: p["t"])
    mae_ia, mae_baseline = publish.score_day(
        [p["obs"] for p in series], [p["ia"] for p in series], [p["baseline"] for p in series]
    )
    lead_hours = [(pd.Timestamp(p["t"]) - issued_ts) / pd.Timedelta(hours=1) for p in series]
    new_entry = {
        "date": entry["date"],
        "status": "ok",
        "series": series,
        "mae_ia": round(mae_ia, 4),
        "mae_baseline": round(mae_baseline, 4),
        "n_points": len(series),
        "max_lead_h": int(round(max(lead_hours))),
    }
    still_pending = [p for p, ok in zip(pending, keep) if not ok]
    if still_pending and not stale:
        new_entry["pending"] = still_pending
    if entry.get("backfilled"):
        new_entry["backfilled"] = True
    return new_entry


def _rescore_pending(station: Station, obs: pd.Series, out_dir: Path, run_date: date) -> None:
    """Complete every history day still carrying `pending` leads — this is what
    makes the 25-48h half of an issue *verified* rather than merely displayed:
    those leads only meet their obs two days after issuance, one day after
    `_score_previous_issue` has come and gone. Called from both scoring paths
    (daily's `_run_station` and backfill's `_backfill_station`) — wherever
    `score_series` can write `pending`, this sweep must be reachable too."""
    history = publish.read_history(out_dir, station.id)
    if not history:
        return
    cutoff = run_date - timedelta(days=PENDING_MAX_AGE_DAYS)
    for entry in history["days"]:
        if not entry.get("pending"):
            continue
        if date.fromisoformat(entry["date"]) >= run_date:
            continue  # this run's own issue: no obs beyond what already scored it
        new_entry = rescore_entry(entry, obs, drop_pending_before=cutoff)
        if new_entry != entry:
            publish.upsert_history(out_dir, station.id, new_entry)


def _score_previous_issue(station: Station, obs: pd.Series, out_dir: Path, run_date: date) -> None:
    """Score a *previous* `latest.json` against today's freshly fetched obs.

    Day label = that issue's own `issued` date — never `run_date` — because
    an issue can be scored on any later run, not necessarily the next day.
    Guard against re-running the same (or a past) `--date`: a `latest.json`
    issued on or after `run_date` was written by *this or a later* run, not a
    genuinely previous one, and scoring it here would invent a day out of a
    single self-matched point (the bug an earlier version of this file had).
    """
    path = out_dir / station.id / "latest.json"
    if not path.exists():
        return
    prev = json.loads(path.read_text())
    issued_ts = pd.Timestamp(prev["issued"])
    if issued_ts.date() >= run_date:
        return
    entry = score_series(obs, prev.get("series") or [], issued_ts)
    publish.upsert_history(out_dir, station.id, entry)


def issue_series(
    station: Station,
    obs: pd.Series,
    t0: pd.Timestamp,
    mfwam: dict,
    forcing: pd.DataFrame,
    models_dir: Path | None,
) -> list[dict]:
    """Pure inference core (no I/O): baseline -> features -> model -> `[{"t","ia",
    "baseline"}]`. Shared by `_run_station` (live forcing, today's issue) and
    `backfill.py` (a-posteriori forcing/obs, a past day's issue) — one code path,
    résolution 1's "ne duplique pas la logique de prédiction"."""
    baseline = _baseline_window(station, obs, t0, mfwam)
    feats = build_features(baseline, obs, t0, forcing)
    pipe = model.load(station.id, models_dir=models_dir)
    pred = model.predict(pipe, feats)
    ia = feats["baseline"].to_numpy() + pred if station.kind == "tide" else pred
    return [
        {"t": iso(t), "ia": round(float(i), 4), "baseline": round(float(b), 4)}
        for t, i, b in zip(feats.index, ia, feats["baseline"])
    ]


def _run_station(
    station: Station,
    run_date: date,
    t0: pd.Timestamp,
    issued: str,
    mfwam: dict,
    out_dir: Path,
    models_dir: Path | None,
    archive_dir: Path,
) -> dict:
    try:
        obs = _fetch_obs(station, run_date)
    except Exception as exc:  # noqa: BLE001 - one station's failure must never be global
        log.warning("%s: obs fetch failed: %s", station.id, exc)
        publish.upsert_history(out_dir, station.id, {"date": run_date.isoformat(), "status": "missing"})
        return {"status": "missing", "reason": str(exc)}

    try:
        # A malformed/truncated latest.json (bad JSON, missing "issued") must
        # not abort today's inference below — scoring the past and issuing
        # today are independent, so a failure here is swallowed, not raised.
        _score_previous_issue(station, obs, out_dir, run_date)
        _rescore_pending(station, obs, out_dir, run_date)
    except Exception as exc:  # noqa: BLE001
        log.warning("%s: scoring the previous issue failed: %s", station.id, exc)

    try:
        forcing = fetch_wind_forecast(station)
        series = issue_series(station, obs, t0, mfwam, forcing, models_dir)
    except Exception as exc:  # noqa: BLE001 - SourceError, a missing model file,
        # sklearn/pandas/utide raising on a degenerate input: none of it may
        # escape and abort the other stations' loop iteration.
        log.warning("%s: inference failed: %s", station.id, exc)
        # Distinct "date" meaning from _score_previous_issue's entry above:
        # this one says "run_date's own issuance failed", not "a past issue
        # could not be scored" — the two can coexist in the same history.json.
        publish.upsert_history(out_dir, station.id, {"date": run_date.isoformat(), "status": "missing"})
        return {"status": "missing", "reason": str(exc)}

    publish.write_latest(out_dir, station.id, issued, series)

    try:
        # Archived *after* a successful issuance only (résolution: a failed
        # station has nothing to archive, no invented empty rows) — see
        # `docs/data-sources.md` for why this corpus exists at all. Must
        # never fail the run: the scoreboard publish above already happened.
        valid_times = pd.DatetimeIndex([pd.Timestamp(p["t"]) for p in series])
        archive.write_day(archive_dir, station.id, t0, valid_times, forcing, source=FORECAST_MODEL)
    except Exception as exc:  # noqa: BLE001 - archiving must never fail the run
        log.warning("%s: archiving served wind forecast failed: %s", station.id, exc)

    return {"status": "ok", "n_points": len(series)}


def run(
    run_date: date,
    out_dir: Path,
    *,
    stations: list[Station] | None = None,
    gate: dict | None = None,
    models_dir: Path | None = None,
    archive_dir: Path | None = None,
) -> dict[str, dict]:
    """Predict, score, publish for `run_date`. Returns `{station_id: {status, ...}}`
    for the *published* (gate-passing) stations only."""
    stations = stations if stations is not None else load_stations()
    gate = gate if gate is not None else load_gate()
    archive_dir = archive_dir if archive_dir is not None else archive.DEFAULT_ARCHIVE_DIR
    t0 = pd.Timestamp(run_date, tz="UTC") + pd.Timedelta(hours=ISSUE_HOUR)
    issued = iso(t0)

    publish.write_stations(out_dir, stations, gate)

    published = [s for s in stations if gate.get(s.id, {}).get("pass", False)]
    wave_stations = [s for s in published if s.kind == "wave"]
    mfwam: dict = {}
    if wave_stations:
        try:
            mfwam = fetch_wave_forecast(wave_stations, run_date, lookback_days=1, horizon_days=3)
        except SourceError as exc:
            log.warning("mfwam fetch failed for all wave stations: %s", exc)

    summary = {
        st.id: _run_station(st, run_date, t0, issued, mfwam, out_dir, models_dir, archive_dir)
        for st in published
    }

    # `issued` (run_date's own nominal issuance instant), not wall-clock
    # `datetime.now()`: rerunning the same `run_date` a second time (real-world
    # idempotence check, GitHub Actions cron re-triggered or manually
    # re-dispatched) must write byte-identical `scores.json` when nothing
    # else changed, or the daily commit step never becomes a true no-op.
    # Same fix intent as backfill.py's "skip on a strict no-op" guard, applied
    # here by making the timestamp itself deterministic per `run_date` instead
    # (daily always writes at least one station's status, so a truthy-summary
    # guard wouldn't skip anything here).
    publish.write_scores(out_dir, [s.id for s in published], issued)
    return summary
