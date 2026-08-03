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

from scoreboard import harmonic, model, publish
from scoreboard.config import Station, load_stations
from scoreboard.features import build_features
from scoreboard.sources import SourceError
from scoreboard.sources.candhis import fetch_wave_obs
from scoreboard.sources.mfwam import fetch_wave_forecast
from scoreboard.sources.waterlevel import fetch_tide_obs
from scoreboard.sources.wind import fetch_wind_forecast

log = logging.getLogger(__name__)

ISSUE_HOUR = 6  # UTC, matches dataset.assemble's training default
OBS_LOOKBACK_DAYS = 4  # wave: >= 24h (mean_err_24h) + margin for a short outage
TIDE_FIT_LOOKBACK_DAYS = 90  # utide needs months, not days, to separate constituents
MIN_TIDE_FIT_DAYS = 30  # same hard floor as scripts/build_dataset.py's `24 * 30`
BASELINE_LOOKBACK_H = 24
BASELINE_HORIZON_H = 48
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

    if not keep.any():
        return {"date": day, "status": "missing"}

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
    return {
        "date": day,
        "status": "ok",
        "series": out_series,
        "mae_ia": round(mae_ia, 4),
        "mae_baseline": round(mae_baseline, 4),
        "n_points": int(keep.sum()),
        # Réserve : ne couvre que les leads matchés par les obs disponibles au
        # moment du scoring (typiquement <= 24h en run quotidien — voir
        # `_fetch_obs`/`OBS_LOOKBACK_DAYS` — mais le plein horizon en backfill,
        # voir `backfill.py`), pas le plein horizon 48h de `gate.mae_model` —
        # pas comparable tel quel.
        "max_lead_h": int(round(lead_hours.max())),
    }


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
    return {"status": "ok", "n_points": len(series)}


def run(
    run_date: date,
    out_dir: Path,
    *,
    stations: list[Station] | None = None,
    gate: dict | None = None,
    models_dir: Path | None = None,
) -> dict[str, dict]:
    """Predict, score, publish for `run_date`. Returns `{station_id: {status, ...}}`
    for the *published* (gate-passing) stations only."""
    stations = stations if stations is not None else load_stations()
    gate = gate if gate is not None else load_gate()
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
        st.id: _run_station(st, run_date, t0, issued, mfwam, out_dir, models_dir) for st in published
    }

    publish.write_scores(out_dir, [s.id for s in published], iso(pd.Timestamp(datetime.now(timezone.utc))))
    return summary
