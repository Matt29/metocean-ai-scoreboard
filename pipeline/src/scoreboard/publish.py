"""Serialize the scoreboard JSON contract — writing only, no fetching/inference.

Four files per run, all wrapped in `{"schema_version": 1, ...}` so an external
consumer (the website, a separate repo) can detect a breaking change:

    data/stations.json          {"stations": [{"id","name","kind","lat","lon",
                                 "unit","published","weak"}]}
    data/<id>/latest.json       {"station","issued","series":[{"t","ia","baseline"}]}
    data/<id>/history.json      {"station","days":[{"date","status",
                                 "series"?,"mae_ia"?,"mae_baseline"?}]}   (90d max)
    data/scores.json            {"updated","stations":[{"id","n_days",
                                 "mae_ia_7d","mae_baseline_7d","mae_ia_30d",
                                 "mae_baseline_30d","mae_ia_all","mae_baseline_all"}]}

`published`/`weak` on every station entry come straight from `models/gate.json`
(read by the caller, passed in here) — a `pass: false` station is still listed
(the site can say "tracked, not yet beating the baseline") but never gets a
`latest.json`/`history.json` written by the daily orchestrator.

All writes are tmp-then-rename in the target directory (atomic on the same
filesystem) so a crash mid-run never leaves a half-written file for the site
to read.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from scoreboard.config import Station

SCHEMA_VERSION = 1
MAX_HISTORY_DAYS = 90
_SCORE_WINDOWS = {"7d": 7, "30d": 30, "all": None}


def score_day(obs, pred_ia, pred_baseline) -> tuple[float, float]:
    """(mae_ia, mae_baseline) — elementwise-aligned, same-length sequences."""
    obs = np.asarray(obs, dtype=float)
    ia = np.asarray(pred_ia, dtype=float)
    baseline = np.asarray(pred_baseline, dtype=float)
    return float(np.abs(ia - obs).mean()), float(np.abs(baseline - obs).mean())


def _atomic_write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n")
    tmp.replace(path)  # rename is atomic within the same directory/filesystem


def _read(path: Path) -> dict | None:
    return json.loads(path.read_text()) if path.exists() else None


def write_stations(out_dir: Path, stations: list[Station], gate: dict) -> dict:
    """`data/stations.json` — every configured station, gate verdict included."""
    payload = {
        "schema_version": SCHEMA_VERSION,
        "stations": [
            {
                "id": s.id,
                "name": s.name,
                "kind": s.kind,
                "lat": s.lat,
                "lon": s.lon,
                "unit": "m",
                "published": bool(gate.get(s.id, {}).get("pass", False)),
                "weak": bool(gate.get(s.id, {}).get("weak", False)),
            }
            for s in stations
        ],
    }
    _atomic_write(out_dir / "stations.json", payload)
    return payload


def write_latest(out_dir: Path, station_id: str, issued: str, series: list[dict]) -> dict:
    """`data/<id>/latest.json` — full overwrite, no history kept here."""
    payload = {
        "schema_version": SCHEMA_VERSION,
        "station": station_id,
        "issued": issued,
        "series": series,
    }
    _atomic_write(out_dir / station_id / "latest.json", payload)
    return payload


def upsert_history(out_dir: Path, station_id: str, day_entry: dict) -> dict:
    """`data/<id>/history.json` — replace-by-date (idempotent), capped at 90 days."""
    path = out_dir / station_id / "history.json"
    payload = _read(path) or {
        "schema_version": SCHEMA_VERSION,
        "station": station_id,
        "days": [],
    }
    by_date = {d["date"]: d for d in payload["days"]}
    by_date[day_entry["date"]] = day_entry
    payload["days"] = sorted(by_date.values(), key=lambda d: d["date"])[-MAX_HISTORY_DAYS:]
    _atomic_write(path, payload)
    return payload


def compute_scores(days: list[dict]) -> dict:
    """MAE aggregates over "ok" days only — a "missing" day must not move them."""
    ok = [d for d in days if d.get("status") == "ok"]
    row = {"n_days": len(ok)}
    for label, n in _SCORE_WINDOWS.items():
        window = ok[-n:] if n else ok
        row[f"mae_ia_{label}"] = round(sum(d["mae_ia"] for d in window) / len(window), 4) if window else None
        row[f"mae_baseline_{label}"] = (
            round(sum(d["mae_baseline"] for d in window) / len(window), 4) if window else None
        )
    return row


def write_scores(out_dir: Path, station_ids: list[str], updated: str) -> dict:
    """`data/scores.json` — recomputed from each station's on-disk history."""
    rows = []
    for station_id in station_ids:
        history = _read(out_dir / station_id / "history.json")
        rows.append({"id": station_id, **compute_scores(history["days"] if history else [])})
    payload = {"schema_version": SCHEMA_VERSION, "updated": updated, "stations": rows}
    _atomic_write(out_dir / "scores.json", payload)
    return payload
