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

Each station is wrapped in its own try/except (résolution 5): a `SourceError`
anywhere in a station's pipeline — obs, baseline, or forcing — marks that
station `"missing"` for the day and never reaches or blocks the others.
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
OBS_LOOKBACK_DAYS = 4  # >= 24h (mean_err_24h) with margin for a short source outage
BASELINE_LOOKBACK_H = 24
BASELINE_HORIZON_H = 48
GATE_PATH = model.MODELS_DIR / "gate.json"


def load_gate(path: Path | None = None) -> dict:
    path = path or GATE_PATH
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def _iso(t: pd.Timestamp) -> str:
    return t.isoformat().replace("+00:00", "Z")


def _fetch_obs(station: Station, run_date: date) -> pd.Series:
    """One request (résolution 5): the whole lookback window in a single call."""
    start = run_date - timedelta(days=OBS_LOOKBACK_DAYS)
    if station.kind == "wave":
        df = fetch_wave_obs(station, start)
        return df["hs"].astype(float).dropna().sort_index()
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
    if past.empty:
        raise SourceError(station.id, "no tide observations to fit a harmonic baseline")
    fitted = harmonic.fit(past, station.lat)
    window = pd.date_range(
        t0 - pd.Timedelta(hours=BASELINE_LOOKBACK_H),
        t0 + pd.Timedelta(hours=BASELINE_HORIZON_H),
        freq="1h",
        tz="UTC",
    )
    return fitted.predict(window)


def _score_previous_issue(station: Station, obs: pd.Series, out_dir: Path) -> None:
    """Score yesterday's `latest.json` against today's freshly fetched obs."""
    path = out_dir / station.id / "latest.json"
    if not path.exists():
        return
    prev = json.loads(path.read_text())
    prev_series = prev.get("series") or []
    day = pd.Timestamp(prev["issued"]).date().isoformat()
    if not prev_series:
        publish.upsert_history(out_dir, station.id, {"date": day, "status": "missing"})
        return

    times = pd.DatetimeIndex([pd.Timestamp(p["t"]) for p in prev_series])
    ia = pd.Series([p["ia"] for p in prev_series], index=times)
    baseline = pd.Series([p["baseline"] for p in prev_series], index=times)
    matched = obs.reindex(times, method="nearest", tolerance=pd.Timedelta("1h"))
    keep = matched.notna()

    if not keep.any():
        publish.upsert_history(out_dir, station.id, {"date": day, "status": "missing"})
        return

    mae_ia, mae_baseline = publish.score_day(matched[keep], ia[keep], baseline[keep])
    series = [
        {
            "t": _iso(t),
            "obs": round(float(matched[t]), 4),
            "ia": round(float(ia[t]), 4),
            "baseline": round(float(baseline[t]), 4),
        }
        for t in times[keep]
    ]
    publish.upsert_history(
        out_dir,
        station.id,
        {
            "date": day,
            "status": "ok",
            "series": series,
            "mae_ia": round(mae_ia, 4),
            "mae_baseline": round(mae_baseline, 4),
        },
    )


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
    except SourceError as exc:
        log.warning("%s: obs fetch failed: %s", station.id, exc)
        publish.upsert_history(out_dir, station.id, {"date": run_date.isoformat(), "status": "missing"})
        return {"status": "missing", "reason": str(exc)}

    _score_previous_issue(station, obs, out_dir)

    try:
        baseline = _baseline_window(station, obs, t0, mfwam)
        forcing = fetch_wind_forecast(station)
        feats = build_features(baseline, obs, t0, forcing)
        pipe = model.load(station.id, models_dir=models_dir)
        pred = model.predict(pipe, feats)
        ia = feats["baseline"].to_numpy() + pred if station.kind == "tide" else pred
        series = [
            {"t": _iso(t), "ia": round(float(i), 4), "baseline": round(float(b), 4)}
            for t, i, b in zip(feats.index, ia, feats["baseline"])
        ]
    except (SourceError, FileNotFoundError, OSError) as exc:
        # FileNotFoundError/OSError: a missing/corrupt model artifact must not
        # crash the whole run either — same "one station down never blocks
        # the others" contract as a SourceError.
        log.warning("%s: inference failed: %s", station.id, exc)
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
    issued = _iso(t0)

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

    publish.write_scores(out_dir, [s.id for s in published], _iso(pd.Timestamp(datetime.now(timezone.utc))))
    return summary
