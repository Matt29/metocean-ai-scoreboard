"""Serialize the scoreboard JSON contract — writing only, no fetching/inference.

Four files per run, all wrapped in `{"schema_version": 1, ...}` so an external
consumer (the website, a separate repo) can detect a breaking change:

    data/stations.json          {"updated","stations": [{"id","name","kind","lat","lon",
                                 "unit","published","weak","baseline_model"?}]}
    data/<id>/latest.json       {"station","issued","series":[{"t","ia","baseline"}]}
    data/<id>/history.json      {"station","days":[{"date","status",
                                 "series"?,"mae_ia"?,"mae_baseline"?,
                                 "n_points"?}]}   (90d max)
    data/scores.json            {"updated","stations":[{"id","status","n_days",
                                 "mae_ia_7d","mae_baseline_7d","mae_ia_30d",
                                 "mae_baseline_30d","mae_ia_90d","mae_baseline_90d",
                                 "mae_ia_all","mae_baseline_all",
                                 "by_lead"|"by_lead_90d":{"h06"|"h12"|"h24"|"h48":
                                 {"mae_ia","mae_baseline","n_points"}},
                                 "metrics_30d"|"metrics_90d":{"rmse_ia","rmse_baseline",
                                 "bias_ia","bias_baseline","r2_ia","r2_baseline",
                                 "n_points"}}]}

Les champs `*_90d` (`mae_ia_90d`, `mae_baseline_90d`, `by_lead_90d`,
`metrics_90d`) sont *additifs* : même forme et mêmes règles de dégradation que
leurs homologues 30d, `schema_version` inchangé (voir `write_latest`). Tant que
l'historique est plus court que 90 jours, la fenêtre 90d contient simplement
tous les jours disponibles — `n_points` dit alors la vérité, et une fenêtre
vide vaut `None`, jamais NaN.

Plus un cinquième fichier, écrit par une autre commande (`archive-obs`, pas
`daily`) et volontairement hors du contrat des stations :

    data/buoys.json             {"updated","since","buoys":[{"id","name",
                                 "lat","lon","wave"}]}

Et un sixième, écrit par `daily` juste après `scores.json` mais délibérément
séparé de lui (pas question de gonfler `scores.json` avec des séries) :

    data/extremes.json          {"updated","stations":[{"id","episodes":[
                                 {"date","obs_peak","t_peak","ia_at_peak",
                                 "baseline_at_peak","peak_error_ia",
                                 "peak_error_baseline","baseline_model"?}]}]}

Et un septième, écrit par `daily` juste après `extremes.json`, hors contrat
JSON versionné (pas de `schema_version`, le site le télécharge tel quel plutôt
que de le désérialiser comme les fichiers ci-dessus) — le lead magnet CSV :

    data/<id>/series.csv        date,t,lead_h,obs,ia,baseline,baseline_model —
                                 une ligne par point des jours "ok" de
                                 l'historique complet, triée par `t` croissant.

`published`/`weak` on every station entry come straight from `models/gate.json`
(read by the caller, passed in here) — a `pass: false` station is still listed
(the site can say "tracked, not yet beating the baseline") but never gets a
`latest.json`/`history.json` written by the daily orchestrator.

All writes are tmp-then-rename in the target directory (atomic on the same
filesystem) so a crash mid-run never leaves a half-written file for the site
to read.
"""

from __future__ import annotations

import csv
import io
import json
import os
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np

from scoreboard.config import Station

SCHEMA_VERSION = 1
MAX_HISTORY_DAYS = 90
# Window sizes in calendar days (not "ok" day counts) — see compute_scores().
_SCORE_WINDOWS = {"7d": 7, "30d": 30, "90d": 90, "all": None}
# Windows getting a point-by-point `by_lead`/`metrics` breakdown, and the
# suffix their keys carry. "30d" keeps its historical unsuffixed `by_lead`.
_BREAKDOWN_WINDOWS = {"30d": "", "90d": "_90d"}
# Emission instant of a day's issue, UTC hour — must match daily.ISSUE_HOUR.
# Duplicated rather than imported: `daily` imports `publish`, not the reverse,
# and this module has no other reason to depend on the orchestrator.
_ISSUE_HOUR = 6
LEAD_BUCKETS = ("h06", "h12", "h24", "h48")  # see compute_lead_breakdown()


def score_day(obs, pred_ia, pred_baseline) -> tuple[float, float]:
    """(mae_ia, mae_baseline) — elementwise-aligned, same-length sequences."""
    obs = np.asarray(obs, dtype=float)
    ia = np.asarray(pred_ia, dtype=float)
    baseline = np.asarray(pred_baseline, dtype=float)
    return float(np.abs(ia - obs).mean()), float(np.abs(baseline - obs).mean())


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # A unique tmp name (not a fixed `<file>.tmp` sibling) so a crash between
    # write and rename never leaves a stale, colliding file for the next run
    # (or a `git add data/` in CI) to pick up; the except cleans up the one
    # case an unhandled write error would otherwise leave behind.
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
        os.replace(tmp_name, path)  # atomic within the same directory/filesystem
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise


def _atomic_write(path: Path, payload: dict) -> None:
    _atomic_write_text(path, json.dumps(payload, indent=2) + "\n")


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


def write_stations(
    out_dir: Path, stations: list[Station], gate: dict, updated: str | None = None
) -> dict:
    """`data/stations.json` — every configured station, gate verdict included.

    `baseline_model` is emitted here as well as in `latest.json`, and that is not
    duplication for its own sake: which physical model a station is measured
    against is a property OF the station, not of today's issue. The site's list
    view reads only `stations.json` + `scores.json` (two requests, by design) —
    without it here, naming the reference in the table would cost one extra
    request per station. Omitted for tide stations, whose baseline is the
    harmonic fit, not a model.

    `updated` is the run's own freshness timestamp (ISO UTC). `daily.run()`
    passes its deterministic `issued` (derived from `run_date`, never
    `datetime.now()`) so a re-run of the same `run_date` writes a byte-identical
    file. A caller without such a value (backfill) preserves whatever `updated`
    is already on disk instead of stamping wall-clock time or clobbering it:
    a no-op backfill must keep `stations.json` byte-identical (same property
    its `write_scores` guard protects), and the site's freshness badge should
    date the *daily* run, the only producer whose staleness means something.
    The key is absent only on a true cold start, before the first daily run.
    """
    path = out_dir / "stations.json"
    updated = updated or (_read(path) or {}).get("updated")
    payload = {"schema_version": SCHEMA_VERSION}
    if updated:
        payload["updated"] = updated
    payload["stations"] = [_station_entry(s, gate.get(s.id, {})) for s in stations]
    _atomic_write(path, payload)
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


def _ok_days_current_baseline(days: list[dict]) -> list[dict]:
    """"ok" days scored against the *current* baseline (see `compute_scores`'s
    docstring for why an outdated baseline is excluded rather than averaged
    in). Shared by `compute_scores` and `compute_lead_breakdown` so the two
    never drift on what counts as a comparable day."""
    ok = [d for d in days if d.get("status") == "ok"]
    current = _current_baseline_model(days)
    if current is not None:
        ok = [d for d in ok if d.get("baseline_model") == current]
    return ok


def _window_days(ok: list[dict], n: int | None) -> list[dict]:
    """Calendar-based slice of `ok`, anchored on its own latest date — not
    wall-clock, so the result stays deterministic from the input alone.
    `n=None` (or an empty `ok`) returns every day, matching the "all" window."""
    anchor = max((date.fromisoformat(d["date"]) for d in ok), default=None)
    if not n or anchor is None:
        return ok
    cutoff = anchor - timedelta(days=n)
    return [d for d in ok if date.fromisoformat(d["date"]) > cutoff]


def _lead_bucket(lead_h: float) -> str | None:
    """Which `LEAD_BUCKETS` slot a point's lead falls in, or `None` outside
    [0, 48] (a matched obs should never sit there, but a corrupt/legacy point
    must not be miscounted into an edge bucket)."""
    if lead_h < 0 or lead_h > 48:
        return None
    if lead_h <= 6:
        return "h06"
    if lead_h <= 12:
        return "h12"
    if lead_h <= 24:
        return "h24"
    return "h48"


def compute_lead_breakdown(window: list[dict]) -> dict:
    """`by_lead`: point-by-point MAE decomposition by lead time (h06=0-6h,
    h12=7-12h, h24=13-24h, h48=25-48h), one bucket set for `scores.json`.

    `window` is the already-filtered, already-windowed day list — the *same
    list* `compute_scores` builds for its "30d" column (`_ok_days_current_baseline`
    + `_window_days`), passed through rather than re-derived here, so the two
    cannot drift on what counts as a comparable day. Publié pour deux fenêtres
    seulement (`by_lead` = 30d, `by_lead_90d` = 90d, cf. `_BREAKDOWN_WINDOWS`) :
    30d reste la fenêtre du gain affiché en tête de site, 90d donne le recul
    saisonnier. Pas de version 7d ni "all" — la lisibilité du contrat prime
    sur l'exhaustivité.

    Un point = une voix, cohérent avec le poids `n_points` des scores
    agrégés : ce n'est pas une MAE journalière repondérée, c'est une MAE
    calculée directement sur les points des `series` des jours retenus. Un
    jour ancien sans `series` détaillée (compat historique) ou un point sans
    `obs` valide est silencieusement ignoré plutôt que de faire échouer la
    publication. Une tranche sans aucun point retombe sur
    `{"mae_ia": None, "mae_baseline": None, "n_points": 0}`.

    Lead d'un point = son `t` moins l'instant d'émission du jour (`date` +
    `_ISSUE_HOUR` UTC) — pas `max_lead_h`, déjà agrégé et donc impossible à
    répartir par tranche.
    """
    err_ia: dict[str, list[float]] = {label: [] for label in LEAD_BUCKETS}
    err_baseline: dict[str, list[float]] = {label: [] for label in LEAD_BUCKETS}
    for day in window:
        series = day.get("series")
        if not series:
            continue
        issued = datetime.combine(date.fromisoformat(day["date"]), datetime.min.time(), timezone.utc)
        issued += timedelta(hours=_ISSUE_HOUR)
        for point in series:
            if point.get("obs") is None:
                continue
            # 3.11+ parses a trailing "Z" natively.
            lead_h = (datetime.fromisoformat(point["t"]) - issued).total_seconds() / 3600
            bucket = _lead_bucket(lead_h)
            if bucket is None:
                continue
            err_ia[bucket].append(abs(point["ia"] - point["obs"]))
            err_baseline[bucket].append(abs(point["baseline"] - point["obs"]))

    return {
        label: {
            "mae_ia": round(float(np.mean(err_ia[label])), 4) if err_ia[label] else None,
            "mae_baseline": round(float(np.mean(err_baseline[label])), 4) if err_baseline[label] else None,
            "n_points": len(err_ia[label]),
        }
        for label in LEAD_BUCKETS
    }


def compute_window_metrics(window: list[dict]) -> dict:
    """`metrics_30d`/`metrics_90d` : RMSE, biais moyen signé (IA/baseline moins
    obs) et R², calculés point à point sur `window` — la *même* liste que
    `compute_lead_breakdown` (voir sa docstring), passée par l'appelant plutôt
    que refiltrée ici, pour la même raison de ne pas pouvoir diverger. Mêmes
    deux fenêtres que `by_lead`, et pas une de plus.

    Point à point et non dérivable des MAE journalières : RMSE et R² ne se
    moyennent pas (contrairement à la MAE, dont la moyenne pondérée par
    `n_points` est exacte) — il faut les recalculer sur les erreurs
    individuelles, pas sur des agrégats journaliers déjà réduits.

    R² = 1 - SS_res/SS_tot avec SS_tot = variance des obs de la fenêtre.
    `None` (jamais NaN/inf dans le JSON) si moins de 2 points ou variance
    nulle (obs constante sur la fenêtre : SS_tot=0, division impossible).

    Mêmes exclusions que `compute_lead_breakdown` : jour sans `series`
    (compat historique) ou point sans `obs` valide, silencieusement ignoré.
    Fenêtre sans aucun point valide -> tout `None`, `n_points` à 0.
    """
    obs_vals: list[float] = []
    ia_vals: list[float] = []
    baseline_vals: list[float] = []
    for day in window:
        series = day.get("series")
        if not series:
            continue
        for point in series:
            if point.get("obs") is None:
                continue
            obs_vals.append(point["obs"])
            ia_vals.append(point["ia"])
            baseline_vals.append(point["baseline"])

    n_points = len(obs_vals)
    if n_points == 0:
        return {
            "rmse_ia": None,
            "rmse_baseline": None,
            "bias_ia": None,
            "bias_baseline": None,
            "r2_ia": None,
            "r2_baseline": None,
            "n_points": 0,
        }

    obs = np.asarray(obs_vals, dtype=float)
    err_ia = np.asarray(ia_vals, dtype=float) - obs
    err_baseline = np.asarray(baseline_vals, dtype=float) - obs
    ss_tot = float(np.sum((obs - obs.mean()) ** 2))
    can_r2 = n_points >= 2 and ss_tot != 0  # invariant for both models, checked once

    def r2(err: np.ndarray) -> float | None:
        return round(1 - float(np.sum(err**2)) / ss_tot, 4) if can_r2 else None

    return {
        "rmse_ia": round(float(np.sqrt(np.mean(err_ia**2))), 4),
        "rmse_baseline": round(float(np.sqrt(np.mean(err_baseline**2))), 4),
        "bias_ia": round(float(np.mean(err_ia)), 4),
        "bias_baseline": round(float(np.mean(err_baseline)), 4),
        "r2_ia": r2(err_ia),
        "r2_baseline": r2(err_baseline),
        "n_points": n_points,
    }


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
    ok = _ok_days_current_baseline(days)
    # Among the "ok" days, how many were reconstructed a posteriori by
    # `scoreboard backfill` rather than scored the day after a live run — the
    # site surfaces this as "dont N jours reconstitués" (résolution 2).
    row = {"n_days": len(ok), "n_days_backfilled": sum(1 for d in ok if d.get("backfilled"))}
    for label, suffix in _BREAKDOWN_WINDOWS.items():
        window = _window_days(ok, _SCORE_WINDOWS[label])
        row[f"by_lead{suffix}"] = compute_lead_breakdown(window)
        row[f"metrics_{label}"] = compute_window_metrics(window)
    for label, n in _SCORE_WINDOWS.items():
        window = _window_days(ok, n)
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


def _fallback_status(history: dict | None) -> str:
    """Freshness status when the caller has no live run summary (e.g. backfill):
    the most recent history day's own status is the best signal available.
    `history["days"]` is kept sorted ascending by `upsert_history`, so the last
    entry is the newest."""
    if not history or not history.get("days"):
        return "missing"
    return history["days"][-1].get("status", "missing")


def write_scores(
    out_dir: Path,
    station_ids: list[str],
    updated: str,
    statuses: dict[str, str] | None = None,
) -> dict:
    """`data/scores.json` — recomputed from each station's on-disk history.

    `statuses` is this run's own per-station `"ok"`/`"missing"` verdict — `daily.run()`
    passes it straight from `_run_station`'s summary, since that is the only place
    that knows whether *today's* issuance succeeded (a station's history can lag a
    day behind that). Callers without such a summary (backfill) fall back to the
    freshness implied by the station's own history.
    """
    rows = []
    for station_id in station_ids:
        history = _read(out_dir / station_id / "history.json")
        status = (statuses or {}).get(station_id) or _fallback_status(history)
        rows.append(
            {"id": station_id, "status": status, **compute_scores(history["days"] if history else [])}
        )
    payload = {"schema_version": SCHEMA_VERSION, "updated": updated, "stations": rows}
    _atomic_write(out_dir / "scores.json", payload)
    return payload


PEAK_EPISODES = 3  # nombre de pics publiés par station


def compute_extreme_episodes(days: list[dict]) -> list[dict]:
    """Les `PEAK_EPISODES` jours de plus fort pic observé, tri décroissant.

    Pas de filtre baseline ici (contrairement à `compute_scores`) : un pic est
    un événement physique observé, pas une moyenne à comparer d'une baseline à
    l'autre — chaque épisode nomme sa propre `baseline_model` quand le jour en
    a un. Seuls les jours `status=="ok"` avec une `series` non vide sont
    éligibles ; un jour sans pic observable n'a rien à publier.

    Le pic du jour = le point de `series` à l'`obs` maximal. `peak_error_*` =
    prédiction moins observation à cet instant (signé : une sous-estimation de
    tempête, plus dangereuse qu'une surestimation, doit rester visible comme
    telle), arrondi à 4 décimales.
    """
    episodes = []
    for day in days:
        if day.get("status") != "ok":
            continue
        series = [p for p in day.get("series") or [] if p.get("obs") is not None]
        if not series:
            continue
        peak = max(series, key=lambda p: p["obs"])
        episode = {
            "date": day["date"],
            "obs_peak": peak["obs"],
            "t_peak": peak["t"],
            "ia_at_peak": peak["ia"],
            "baseline_at_peak": peak["baseline"],
            "peak_error_ia": round(peak["ia"] - peak["obs"], 4),
            "peak_error_baseline": round(peak["baseline"] - peak["obs"], 4),
        }
        if day.get("baseline_model"):
            episode["baseline_model"] = day["baseline_model"]
        episodes.append(episode)
    episodes.sort(key=lambda e: e["obs_peak"], reverse=True)
    return episodes[:PEAK_EPISODES]


def write_extremes(out_dir: Path, station_ids: list[str], updated: str) -> dict:
    """`data/extremes.json` — recomputed from each station's on-disk history,
    même lecture que `write_scores`. Fichier séparé exprès (voir docstring de
    module) : un pic est un événement, pas une agrégation, et `scores.json` ne
    doit pas grossir pour ça.
    """
    rows = []
    for station_id in station_ids:
        history = _read(out_dir / station_id / "history.json")
        rows.append({"id": station_id, "episodes": compute_extreme_episodes(history["days"] if history else [])})
    payload = {"schema_version": SCHEMA_VERSION, "updated": updated, "stations": rows}
    _atomic_write(out_dir / "extremes.json", payload)
    return payload


SERIES_CSV_HEADER = ("date", "t", "lead_h", "obs", "ia", "baseline", "baseline_model")


def compute_series_csv(days: list[dict]) -> str:
    """`series.csv` text for one station's full history — every point of every
    `status=="ok"` day, sorted by `t` ascending. Pure/no I/O, mirroring
    `compute_extreme_episodes`, so `write_series_csv` and tests share one
    source of truth for the content.

    `lead_h` is the point's `t` minus that day's issue instant (`date` +
    `_ISSUE_HOUR` UTC), rounded to the nearest hour — same definition as
    `compute_lead_breakdown`. `baseline_model` is the day's own, or the empty
    string when absent (tide, or a history written before Task 6): a CSV has
    no `null`, and an empty cell reads unambiguously as "no named model" to a
    spreadsheet.
    """
    rows = []
    for day in days:
        if day.get("status") != "ok":
            continue
        issued = datetime.combine(date.fromisoformat(day["date"]), datetime.min.time(), timezone.utc)
        issued += timedelta(hours=_ISSUE_HOUR)
        baseline_model = day.get("baseline_model", "")
        for point in day.get("series") or []:
            lead_h = round((datetime.fromisoformat(point["t"]) - issued).total_seconds() / 3600)
            rows.append(
                (day["date"], point["t"], lead_h, point.get("obs"), point["ia"], point["baseline"], baseline_model)
            )
    rows.sort(key=lambda r: r[1])

    buf = io.StringIO(newline="")
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(SERIES_CSV_HEADER)
    writer.writerows(rows)
    return buf.getvalue()


def write_series_csv(out_dir: Path, station_id: str) -> str:
    """`data/<id>/series.csv` — the lead-magnet export, recomputed from the
    station's on-disk `history.json` (empty history -> header-only file, not
    an error: `daily.run()` calls this for every published station right
    after `write_extremes`, whether or not that station has history yet).
    Hors contrat JSON (voir docstring de module) : le site le télécharge tel
    quel, il ne le désérialise pas comme les autres fichiers d'ici.
    """
    history = _read(out_dir / station_id / "history.json")
    text = compute_series_csv(history["days"] if history else [])
    _atomic_write_text(out_dir / station_id / "series.csv", text)
    return text
