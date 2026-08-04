"""Serialize the scoreboard JSON contract — writing only, no fetching/inference.

Four files per run, all wrapped in `{"schema_version": 1, ...}` so an external
consumer (the website, a separate repo) can detect a breaking change:

    data/stations.json          {"stations": [{"id","name","kind","lat","lon",
                                 "unit","published","weak","baseline_model"?}]}
    data/<id>/latest.json       {"station","issued","series":[{"t","ia","baseline"}]}
    data/<id>/history.json      {"station","days":[{"date","status",
                                 "series"?,"mae_ia"?,"mae_baseline"?,
                                 "n_points"?}]}   (90d max)
    data/scores.json            {"updated","stations":[{"id","n_days",
                                 "mae_ia_7d","mae_baseline_7d","mae_ia_30d",
                                 "mae_baseline_30d","mae_ia_all","mae_baseline_all"}]}

Plus un cinquième fichier, écrit par une autre commande (`archive-obs`, pas
`daily`) et volontairement hors du contrat des stations :

    data/buoys.json             {"updated","since","buoys":[{"id","name",
                                 "lat","lon","wave"}]}

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
import os
import tempfile
from datetime import date, timedelta
from pathlib import Path

import numpy as np

from scoreboard.config import Station

SCHEMA_VERSION = 1
MAX_HISTORY_DAYS = 90
# Window sizes in calendar days (not "ok" day counts) — see compute_scores().
_SCORE_WINDOWS = {"7d": 7, "30d": 30, "all": None}


def score_day(obs, pred_ia, pred_baseline) -> tuple[float, float]:
    """(mae_ia, mae_baseline) — elementwise-aligned, same-length sequences."""
    obs = np.asarray(obs, dtype=float)
    ia = np.asarray(pred_ia, dtype=float)
    baseline = np.asarray(pred_baseline, dtype=float)
    return float(np.abs(ia - obs).mean()), float(np.abs(baseline - obs).mean())


def _atomic_write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # A unique tmp name (not a fixed `<file>.tmp` sibling) so a crash between
    # write and rename never leaves a stale, colliding file for the next run
    # (or a `git add data/` in CI) to pick up; the except cleans up the one
    # case an unhandled write error would otherwise leave behind.
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(json.dumps(payload, indent=2) + "\n")
        os.replace(tmp_name, path)  # atomic within the same directory/filesystem
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise


def _read(path: Path) -> dict | None:
    return json.loads(path.read_text()) if path.exists() else None


# L'unité de la grandeur notée, par `kind`. Elle est publiée plutôt que déduite
# côté site : `kind` seul ne la donne pas, et une station de vent servie en
# mètres est une erreur de fait sur une donnée publique.
UNIT = {"wave": "m", "tide": "m", "wind": "m/s"}


def _station_entry(s: Station, gate: dict) -> dict:
    entry = {
        "id": s.id,
        "name": s.name,
        "kind": s.kind,
        "lat": s.lat,
        "lon": s.lon,
        "unit": UNIT[s.kind],
        "published": bool(gate.get("pass", False)),
        "weak": bool(gate.get("weak", False)),
    }
    # Omis plutôt que `null` — même forme optionnelle que dans `write_latest`.
    if gate.get("baseline_model"):
        entry["baseline_model"] = gate["baseline_model"]
    return entry


def write_stations(out_dir: Path, stations: list[Station], gate: dict) -> dict:
    """`data/stations.json` — every configured station, gate verdict included.

    `baseline_model` is emitted here as well as in `latest.json`, and that is not
    duplication for its own sake: which physical model a station is measured
    against is a property OF the station, not of today's issue. The site's list
    view reads only `stations.json` + `scores.json` (two requests, by design) —
    without it here, naming the reference in the table would cost one extra
    request per station. Omitted for tide stations, whose baseline is the
    harmonic fit, not a model.
    """
    payload = {
        "schema_version": SCHEMA_VERSION,
        "stations": [_station_entry(s, gate.get(s.id, {})) for s in stations],
    }
    _atomic_write(out_dir / "stations.json", payload)
    return payload


def write_buoys(out_dir: Path, buoys: list[dict], *, updated: str, since: str | None) -> dict:
    """`data/buoys.json` — les bouées Météo-France dont on archive les observations.

    Délibérément *pas* dans `stations.json` : ce ne sont pas des stations du
    scoreboard. Rien n'y est prévu, rien n'y est scoré — seules leurs
    observations sont collectées, en vue du premier entraînement Méditerranée
    (demande produit 4). Les mélanger aux stations les ferait passer pour
    mesurées ; un fichier séparé laisse la carte du site les montrer pour ce
    qu'elles sont, un réseau d'observation en cours de constitution.

    `since` est le premier jour archivé, c'est-à-dire l'âge réel du corpus —
    la seule information qui dise quand un entraînement devient envisageable.
    """
    payload = {
        "schema_version": SCHEMA_VERSION,
        "updated": updated,
        "since": since,
        "buoys": buoys,
    }
    _atomic_write(out_dir / "buoys.json", payload)
    return payload


def write_latest(
    out_dir: Path,
    station_id: str,
    issued: str,
    series: list[dict],
    baseline_model: str | None = None,
) -> dict:
    """`data/<id>/latest.json` — full overwrite, no history kept here.

    `baseline_model` (the Open-Meteo wave model this issue's baseline came from)
    is *additive*: absent for tide, absent for anything issued before Task 6, and
    it does not move `schema_version` — the live site reads the other keys and
    must keep working untouched.
    """
    payload = {
        "schema_version": SCHEMA_VERSION,
        "station": station_id,
        "issued": issued,
        "series": series,
    }
    if baseline_model:
        payload["baseline_model"] = baseline_model
    _atomic_write(out_dir / station_id / "latest.json", payload)
    return payload


def read_history(out_dir: Path, station_id: str) -> dict | None:
    """`history.json` as written by `upsert_history`, or `None` if it doesn't exist
    yet — used by `backfill.py` to find which days are missing (résolution 5)."""
    return _read(out_dir / station_id / "history.json")


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


def _current_baseline_model(days: list[dict]) -> str | None:
    """The baseline this station serves *now*: the one named by its most recent
    day entry that names one.

    Derived from the history itself rather than passed in from the artefact, so
    there is exactly one source of truth: the days being averaged *are* the
    record of which baseline produced them. `None` for tide (harmonic, never
    named) and for a history written entirely before Task 6.
    """
    return next(
        (
            d["baseline_model"]
            for d in sorted(days, key=lambda d: d["date"], reverse=True)
            if d.get("baseline_model")
        ),
        None,
    )


def _score_weight(day: dict) -> int:
    """Nombre d'observations valides représentées par une MAE journalière.

    Le contrat courant de ``history.json`` pose ``n_points`` comme un entier
    strictement positif. Les anciens historiques n'ont pas ce champ : ils
    conservent donc le poids unitaire de l'ancienne moyenne par jour. La même
    solution de repli s'applique aux valeurs invalides (zéro, négatives,
    booléens, fractions, texte et non-finis) afin qu'une entrée corrompue ne
    fasse ni tomber la publication ni disparaître silencieusement des scores.
    """
    value = day.get("n_points", 1)
    if isinstance(value, (bool, str, bytes)):
        return 1
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 1
    if not np.isfinite(number) or number <= 0 or not number.is_integer():
        return 1
    return int(number)


def compute_scores(days: list[dict]) -> dict:
    """MAE aggregates over "ok" days only — a "missing" day must not move them.

    Chaque MAE journalière est pondérée par son ``n_points`` : les scores
    publics donnent donc le même poids à chaque observation/heure valide, pas
    à chaque jour. Pour rester compatible avec les historiques publiés avant
    ``n_points``, une entrée sans compteur (ou avec compteur invalide) reçoit
    un poids de un jour. Cette règle est additive au schéma 1.

    Windows are calendar-based, anchored on the latest "ok" date (not
    wall-clock, so the function stays deterministic from its input): "7d"
    means every ok day within 7 calendar days of that anchor, regardless of
    gaps — not simply the last 7 ok entries.

    Days produced against a *different* baseline than the current one are
    excluded outright: `mae_baseline` (and therefore the gain the site shows)
    is only meaningful against one baseline at a time, and averaging MFWAM days
    with best-wave-model days would publish a hybrid number nobody could
    interpret. Those days stay in `history.json` and keep being served for
    their series — they simply do not feed the windows. Expect the wave windows
    to empty out the day the baseline changes and refill from there; an empty
    window already yields `None`, not a division by zero.
    """
    ok = [d for d in days if d.get("status") == "ok"]
    current = _current_baseline_model(days)
    if current is not None:
        ok = [d for d in ok if d.get("baseline_model") == current]
    # Among the "ok" days, how many were reconstructed a posteriori by
    # `scoreboard backfill` rather than scored the day after a live run — the
    # site surfaces this as "dont N jours reconstitués" (résolution 2).
    row = {"n_days": len(ok), "n_days_backfilled": sum(1 for d in ok if d.get("backfilled"))}
    anchor = max((date.fromisoformat(d["date"]) for d in ok), default=None)
    for label, n in _SCORE_WINDOWS.items():
        if n and anchor is not None:
            cutoff = anchor - timedelta(days=n)
            window = [d for d in ok if date.fromisoformat(d["date"]) > cutoff]
        else:
            window = ok
        weights = [_score_weight(d) for d in window]
        total_weight = sum(weights)
        row[f"mae_ia_{label}"] = (
            round(
                sum(day["mae_ia"] * weight for day, weight in zip(window, weights))
                / total_weight,
                4,
            )
            if window
            else None
        )
        row[f"mae_baseline_{label}"] = (
            round(
                sum(day["mae_baseline"] * weight for day, weight in zip(window, weights))
                / total_weight,
                4,
            )
            if window
            else None
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
