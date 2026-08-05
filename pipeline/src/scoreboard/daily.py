"""Daily orchestration: predict, score yesterday, publish — one run per calendar day.

For each station whose gate verdict is `pass: true` (résolution 2 — a station
that loses to its own baseline is never published):

1. Fetch the station's observations (one request, see `sources.candhis` /
   `sources.waterlevel` quotas, `OBS_LOOKBACK_DAYS` deep whatever the kind) and
   use them to score the predictions this
   station published *yesterday* (read back from its own `latest.json`,
   matched to observations by nearest hour) — the scored result becomes a new
   `history.json` day entry.
2. Build today's baseline: for a `wave` station, the Open-Meteo wave model the
   artefact was *trained against* (`baseline_model`, one marine request per
   station) — for a `tide` station, the harmonic constants
   persisted in `models/<station>-harmonic.joblib`, served as long as they are
   younger than `harmonic.REFIT_DAYS` (une analyse harmonique décrit un *site*,
   pas une journée — le SHOM publie des constantes et les ports s'en servent des
   années). Passé cet âge la station est marquée missing plutôt que servie
   silencieusement par un cron mort.
3. Fetch the atmospheric forcing — the 3 candidate models for wave, the single
   ARPEGE run for tide — and run inference through the trained model. The
   feature columns built here must match the artefact's `feature_columns`
   exactly, or the station is marked missing rather than served a frame the
   model was never fitted on.
4. Publish today's `latest.json`.
5. Archive the served wind forecast (`archive.write_day`, Task A1) for every
   station that reached step 4 — the corpus a future retrain needs to measure
   what a *real* +48 h forecast costs. Training now uses past ARPEGE runs rather
   than ERA5, but Open-Meteo's Historical Forecast API concatenates its freshest
   runs, so those "forecasts" are near-analysis (see `docs/plan-dev-modele.md`).
   This archive is the only honest instrument for that gap. A failure here is
   logged, never allowed to undo the publish above.

Each station is wrapped in its own try/except (résolution 5): *any* exception
anywhere in a station's pipeline — obs, scoring, baseline, forcing, or model —
marks that station `"missing"` for the day and never reaches or blocks the
others. A history day entry's `"date"` means one of two distinct things,
both documented at the call site: the day a *previous* issue is finally
scored (keyed by *that issue's own* `issued` date, however long ago it was
issued) versus the day *this run* failed to issue anything at all (keyed by
`run_date`) — a single run can write both in the same call.

Une seule fenêtre d'obs depuis que les constantes harmoniques sont persistées
(2026-08-04) : `OBS_LOOKBACK_DAYS`, qui couvre les besoins du feature
engineering (`last_err`/`mean_err_24h`) et le scoring de l'émission
précédente. La marée avait la sienne, deux ans de REFMAR retéléchargés chaque
matin (~50 requêtes, ~160 Mo, ~50 s) pour ré-ajuster une analyse qui ne bouge
pas d'un jour sur l'autre ; cette profondeur-là vit maintenant dans
`scripts/fit_harmonic.py`, à la cadence `harmonic.REFIT_DAYS`.
"""

from __future__ import annotations

import json
import logging
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from scoreboard import archive, harmonic, model, publish
from scoreboard.config import Station, load_stations
from scoreboard.features import build_features
from scoreboard.sources import SourceError
from scoreboard.sources.candhis import fetch_wave_obs
from scoreboard.sources.marine import fetch_wave_models_forecast
from scoreboard.sources.mfobs import fetch_wind_obs
from scoreboard.sources.waterlevel import fetch_tide_obs
from scoreboard.sources.wind import (
    MULTI_FORCING_COLUMNS,
    TIDE_FORECAST_MODEL,
    WIND_MODEL_COLUMNS,
    fetch_wind_forecast,
    fetch_wind_models_forecast,
)

log = logging.getLogger(__name__)

ISSUE_HOUR = 6  # UTC, matches dataset.assemble's training default
OBS_LOOKBACK_DAYS = 4  # wave: >= 24h (mean_err_24h) + margin for a short outage
BASELINE_LOOKBACK_H = 24
BASELINE_HORIZON_H = 48
# `archive.write_day`'s `source` for the wave path: the forcing archived there
# is the 3-model frame, not any single named run (unlike tide's TIDE_FORECAST_MODEL).
MULTI_FORCING_SOURCE = "openmeteo:multi"
# Column prefix of the per-model baseline candidates, by station kind. A `tide`
# station is absent on purpose: its baseline is the station's persisted
# harmonic constants, not a column picked out of a multi-model frame.
MODEL_PREFIX = {"wave": "hs_", "wind": "ws_"}
# A day scored the morning after its issue only meets ~24h of its own leads —
# the 25-48h tail stays "pending" in its history entry and is completed by
# `_rescore_pending` on later runs, as obs catch up. Beyond this age, every
# lead of the issue (<= +48h = date+2d) predates the daily obs window
# (`run_date - OBS_LOOKBACK_DAYS`), so no daily run can ever match it again:
# drop the dead weight instead of carrying it forever.
PENDING_MAX_AGE_DAYS = OBS_LOOKBACK_DAYS + 2
GATE_PATH = model.MODELS_DIR / "gate.json"


class GateConfigurationError(RuntimeError):
    """The configured stations cannot safely be published from this gate."""


class DailyRunError(RuntimeError):
    """Every station selected for publication failed during a daily run."""

    def __init__(self, run_date: date, summary: dict[str, dict]) -> None:
        self.run_date = run_date
        self.summary = summary
        super().__init__(f"no gate-passing station was published for {run_date.isoformat()}")


def load_gate(path: Path | None = None) -> dict:
    path = path or GATE_PATH
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def validate_gate(stations: list[Station], gate: dict) -> None:
    """Refuse a stale or partial gate before it can change public metadata."""
    if not isinstance(gate, dict) or not gate:
        raise GateConfigurationError("gate is missing or empty")

    missing = [station.id for station in stations if station.id not in gate]
    malformed = [
        station.id
        for station in stations
        if station.id in gate
        and (
            not isinstance(gate[station.id], dict)
            or any(
                not isinstance(gate[station.id].get(field), bool)
                for field in ("pass", "weak")
            )
        )
    ]
    if missing or malformed:
        details = []
        if missing:
            details.append(f"missing station verdicts: {', '.join(missing)}")
        if malformed:
            details.append(f"invalid station verdicts: {', '.join(malformed)}")
        raise GateConfigurationError("gate is incomplete: " + "; ".join(details))

    if not any(gate[station.id]["pass"] for station in stations):
        raise GateConfigurationError("gate publishes no configured station")


def iso(t: pd.Timestamp) -> str:
    return t.isoformat().replace("+00:00", "Z")


def _fetch_obs(station: Station, run_date: date) -> pd.Series:
    """One station-level fetch (résolution 5), `OBS_LOOKBACK_DAYS` deep quelle que
    soit la source : depuis que les constantes harmoniques sont persistées, la
    marée n'a plus besoin de ses deux ans d'historique au quotidien (une requête
    REFMAR au lieu de ~50, ~0,3 Mo au lieu de ~160).

    Le dispatch porte sur `station.source`, pas sur `station.kind` : c'est la
    source qui détermine à qui on parle, et deux sources peuvent servir le même
    `kind` (les bouées Méditerranée serviront de la houle sans être Candhis).
    Une source sans collecteur lève, au lieu d'atterrir silencieusement chez
    celui du kind — c'est ce qui rendrait faux, et non manquant, un jour publié.
    """
    if station.source == "candhis":
        start = run_date - timedelta(days=OBS_LOOKBACK_DAYS)
        df = fetch_wave_obs(station, start)
        return df["hs"].astype(float).dropna().sort_index()
    if station.source == "mfobs":
        # ~30 requêtes (DPObs ne sert qu'une heure à la fois, cf. `sources.mfobs`)
        # — le coût qui plafonne le nombre de stations vent publiables.
        start = run_date - timedelta(days=OBS_LOOKBACK_DAYS)
        df = fetch_wind_obs(station, start, date_end=run_date)
        return df["wind_speed"].astype(float).dropna().sort_index()
    if station.source == "shom":
        start = run_date - timedelta(days=OBS_LOOKBACK_DAYS)
        df = fetch_tide_obs(station, start, date_end=run_date + timedelta(days=1))
        return df["level"].astype(float).dropna().sort_index()
    raise SourceError(station.id, f"aucun collecteur d'obs pour la source {station.source!r}")




def refit_harmonic(
    station: Station, today: date, models_dir: Path | None = None
) -> harmonic.HarmonicModel:
    """Ajuste les constantes sur `FIT_LOOKBACK_DAYS` d'obs et les persiste.

    Le seul endroit du pipeline de production qui paie encore les deux ans de
    REFMAR (~50 requêtes, ~160 Mo par station) — une fois par semestre au lieu
    d'une fois par jour. `scripts/fit_harmonic.py` n'est qu'une CLI par-dessus :
    ajuster à la main et ajuster en production doivent rester le même code.
    """
    end = today + timedelta(days=1)
    obs = fetch_tide_obs(station, end - timedelta(days=harmonic.FIT_LOOKBACK_DAYS), date_end=end)
    level = obs["level"].dropna()
    if not harmonic.enough_for_fit(level):
        # Station neuve : pas d'artefact, donc `missing` au quotidien jusqu'à ses
        # deux ans d'obs. Refusé bruyamment, jamais ajusté sur moins. Le plancher
        # est celui de l'entraînement, pas un second réglage (`harmonic`).
        raise SourceError(
            station.id, f"seulement {len(level)}h d'obs — pas d'ajustement harmonique"
        )
    fitted = harmonic.fit(level, station.lat)
    fitted.save(harmonic.artifact_path(station.id, models_dir))
    log.info("%s: constantes harmoniques ré-ajustées sur %sh, fit daté du %s",
             station.id, len(level), f"{fitted.fitted_at:%Y-%m-%d}")
    return fitted


def _ensure_harmonic(station: Station, today: date, models_dir: Path | None = None) -> None:
    """Ré-ajuste les constantes si elles manquent ou dépassent `REFIT_DAYS`.

    Le run se rafraîchit lui-même plutôt que de dépendre d'un cron séparé à
    maintenir : la cadence servie en production devient alors identique à celle
    que `causal_predict` rejoue à l'entraînement **par construction**, et non
    parce que deux réglages sont d'accord. C'était le vrai risque de la
    conception (voir `docs/plan-dev-modele.md`).

    La péremption reste vérifiée en aval dans `_baseline_window`, et ce n'est pas
    une redondance : `fitted_at` date la **dernière observation vue**, pas
    l'instant du fit. Un flux REFMAR en retard peut donc produire un artefact
    déjà périmé sans que rien n'ait échoué ici. L'amont rafraîchit, l'aval
    refuse — deux pannes différentes, une seule cadence.
    """
    path = harmonic.artifact_path(station.id, models_dir)
    if path.exists():
        age = (pd.Timestamp(today, tz="UTC") - harmonic.HarmonicModel.load(path).fitted_at).days
        if age <= harmonic.REFIT_DAYS:
            return
        log.info("%s: constantes vieilles de %s j (> %s) — ré-ajustement",
                 station.id, age, harmonic.REFIT_DAYS)
    refit_harmonic(station, today, models_dir)


def _baseline_window(
    station: Station,
    t0: pd.Timestamp,
    models: pd.DataFrame | None,
    baseline_model: str | None,
    models_dir: Path | None = None,
) -> pd.Series:
    """`[t0-24h, t0+48h]` baseline series — one physical model's column, or the
    station's persisted harmonic constants.

    `baseline_model` is the artefact's own (`models/<station>.joblib`), never a
    module default: serving a station off a different physical model than the one
    it was trained to correct is a silent, unmeasurable regression.
    """
    lo = t0 - pd.Timedelta(hours=BASELINE_LOOKBACK_H)
    hi = t0 + pd.Timedelta(hours=BASELINE_HORIZON_H)
    if prefix := MODEL_PREFIX.get(station.kind):
        if not baseline_model:
            raise SourceError(station.id, "artefact carries no baseline_model — retrain needed")
        col = f"{prefix}{baseline_model}"
        if models is None or col not in models.columns:
            raise SourceError(station.id, f"model frame has no {col!r} column")
        baseline = models[col].astype(float).dropna()
        if baseline.empty:
            raise SourceError(station.id, f"{col} is entirely null")
        # The fetch covers a wider margin than the model was trained on; clip back
        # to the trained horizon so `lead_h` never extrapolates past 48h.
        return baseline[(baseline.index > lo) & (baseline.index <= hi)]

    # Marée : les constantes persistées, jamais un fit du jour. La péremption est
    # exactement `harmonic.REFIT_DAYS`, la cadence que `causal_predict` rejoue à
    # l'entraînement — tolérer un artefact plus vieux, ce serait servir une
    # baseline plus périmée que celle sur laquelle le modèle a été noté.
    # Artefact absent (station neuve, jamais ajustée) : le `FileNotFoundError`
    # remonte tel quel à `_run_station`, qui marque la station missing en
    # nommant le fichier manquant — pas la peine de le rhabiller.
    fitted = harmonic.HarmonicModel.load(harmonic.artifact_path(station.id, models_dir))
    age = (t0 - fitted.fitted_at).days
    if age > harmonic.REFIT_DAYS:
        raise SourceError(
            station.id,
            f"constantes harmoniques vieilles de {age} j (> {harmonic.REFIT_DAYS} j) — "
            "le ré-ajustement du run a dû échouer, voir les logs",
        )
    window = pd.date_range(
        t0 - pd.Timedelta(hours=BASELINE_LOOKBACK_H),
        t0 + pd.Timedelta(hours=BASELINE_HORIZON_H),
        freq="1h",
        tz="UTC",
    )
    return fitted.predict(window)


def score_series(
    obs: pd.Series,
    series: list[dict],
    issued_ts: pd.Timestamp,
    baseline_model: str | None = None,
) -> dict:
    """Pure scoring core (no I/O): match an issued `series` (`[{"t","ia","baseline"}]`)
    against `obs` (nearest hour, 1h tolerance) and build the `history.json` day entry.

    Shared by `_score_previous_issue` (reads `series` back off a `latest.json` written
    a day earlier) and `backfill.py` (scores a freshly regenerated `series` immediately,
    against the deep a-posteriori obs already held in memory — résolution 1, no second
    scoring code path).

    `baseline_model` names the wave model the scored baseline came from. It is
    optional and only recorded when known: a tide day never has one, and neither
    does an issue published before Task 6 — the key is absent rather than guessed.
    """
    day = issued_ts.date().isoformat()
    if not series:
        return _with_baseline_model({"date": day, "status": "missing"}, baseline_model)

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
    return _with_baseline_model(entry, baseline_model)


def _with_baseline_model(entry: dict, baseline_model: str | None) -> dict:
    """Additive key, only when known — see `score_series`."""
    if baseline_model:
        entry["baseline_model"] = baseline_model
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
    return _with_baseline_model(new_entry, entry.get("baseline_model"))


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
    # `.get`, not `[...]`: a `latest.json` written before Task 6 has no
    # `baseline_model` key at all and must still be scored, not crash the sweep.
    entry = score_series(obs, prev.get("series") or [], issued_ts, prev.get("baseline_model"))
    publish.upsert_history(out_dir, station.id, entry)


def issue_series(
    station: Station,
    obs: pd.Series,
    t0: pd.Timestamp,
    models: pd.DataFrame | None,
    forcing: pd.DataFrame,
    models_dir: Path | None,
) -> tuple[list[dict], str | None]:
    """Pure inference core (no I/O): baseline -> features -> model -> `([{"t","ia",
    "baseline"}], baseline_model)`. Shared by `_run_station` (live forcing, today's
    issue) and `backfill.py` (a-posteriori forcing/obs, a past day's issue) — one
    code path, résolution 1's "ne duplique pas la logique de prédiction".

    The artefact drives everything about a multi-model path: which Open-Meteo model
    is the baseline, and which feature columns the estimator was fitted on. A frame
    that does not match those columns exactly is refused (`SourceError` — the
    caller marks the station missing) rather than silently reordered or subset by
    `model.predict`: a model asked to correct a different baseline, or fed a
    column list from another training generation, produces plausible garbage.
    """
    artifact = model.load_artifact(station.id, models_dir=models_dir)
    baseline_model = artifact["baseline_model"]
    baseline = _baseline_window(station, t0, models, baseline_model, models_dir)
    feats = build_features(baseline, obs, t0, forcing, models=models)
    if list(feats.columns) != list(artifact["feature_columns"]):
        raise SourceError(
            station.id,
            f"feature columns {list(feats.columns)} do not match the artefact's "
            f"{list(artifact['feature_columns'])}",
        )
    pred = model.predict(artifact["model"], feats)
    ia = feats["baseline"].to_numpy() + pred if station.kind == "tide" else pred
    series = [
        {"t": iso(t), "ia": round(float(i), 4), "baseline": round(float(b), 4)}
        for t, i, b in zip(feats.index, ia, feats["baseline"])
    ]
    return series, baseline_model


def _fetch_inputs(station: Station) -> tuple[pd.DataFrame | None, pd.DataFrame, str]:
    """`(models, forcing, archive source)` for one station.

    Wave: the 5 wave models (baseline + features) and the 3 candidate wind runs.
    Wind: **one** request — the per-model wind speed is the baseline and the u/v
    of the same models is the forcing, and Open-Meteo returns both in the same
    payload, so `with_speeds=True` splits one response instead of paying twice.
    Tide: no model frame, and a single named run — `ecmwf_ifs025` since
    2026-08-04, because it is the only model whose past runs the training leg can
    replay stratified by age (`sources.wind`). The tide path stays deliberately
    untouched by the multi-model switch.
    """
    if station.kind == "wave":
        return (
            fetch_wave_models_forecast(station),
            fetch_wind_models_forecast(station),
            MULTI_FORCING_SOURCE,
        )
    if station.kind == "wind":
        frame = fetch_wind_models_forecast(station, with_speeds=True)
        return frame[WIND_MODEL_COLUMNS], frame[MULTI_FORCING_COLUMNS], MULTI_FORCING_SOURCE
    return None, fetch_wind_forecast(station), TIDE_FORECAST_MODEL


def _run_station(
    station: Station,
    run_date: date,
    t0: pd.Timestamp,
    issued: str,
    out_dir: Path,
    models_dir: Path | None,
    archive_dir: Path,
) -> dict:
    try:
        # Avant tout le reste : des constantes fraîches, sinon la station est
        # `missing` plus bas. Une fois par semestre, ce seul appel coûte les
        # ~50 s de REFMAR que le run quotidien ne paie plus.
        if station.kind == "tide":
            _ensure_harmonic(station, run_date, models_dir)
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
        model_frame, forcing, forcing_source = _fetch_inputs(station)
        series, baseline_model = issue_series(station, obs, t0, model_frame, forcing, models_dir)
    except Exception as exc:  # noqa: BLE001 - SourceError, a missing model file,
        # sklearn/pandas/utide raising on a degenerate input: none of it may
        # escape and abort the other stations' loop iteration.
        log.warning("%s: inference failed: %s", station.id, exc)
        # Distinct "date" meaning from _score_previous_issue's entry above:
        # this one says "run_date's own issuance failed", not "a past issue
        # could not be scored" — the two can coexist in the same history.json.
        publish.upsert_history(out_dir, station.id, {"date": run_date.isoformat(), "status": "missing"})
        return {"status": "missing", "reason": str(exc)}

    publish.write_latest(out_dir, station.id, issued, series, baseline_model=baseline_model)

    try:
        # Archived *after* a successful issuance only (résolution: a failed
        # station has nothing to archive, no invented empty rows) — see
        # `docs/data-sources.md` for why this corpus exists at all. Must
        # never fail the run: the scoreboard publish above already happened.
        # Wave frame (`hs_*`, includes the baseline itself) alongside the wind
        # forcing: without it the anti-skew corpus is missing 5 of the 18 wave
        # features a future retrain needs (Task 7 review).
        archived = forcing if model_frame is None else pd.concat([forcing, model_frame], axis=1)
        valid_times = pd.DatetimeIndex([pd.Timestamp(p["t"]) for p in series])
        archive.write_day(archive_dir, station.id, t0, valid_times, archived, source=forcing_source)
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
    validate_gate(stations, gate)
    archive_dir = archive_dir if archive_dir is not None else archive.DEFAULT_ARCHIVE_DIR
    t0 = pd.Timestamp(run_date, tz="UTC") + pd.Timedelta(hours=ISSUE_HOUR)
    issued = iso(t0)

    # `issued` here doubles as `stations.json`'s freshness marker — same
    # determinism reasoning as `write_scores`'s call below (see the comment
    # there): a re-run of the same `run_date` must write byte-identical output.
    publish.write_stations(out_dir, stations, gate, updated=issued)

    published = [s for s in stations if gate.get(s.id, {}).get("pass", False)]
    # No shared pre-fetch any more: every source is one request per station
    # (Open-Meteo marine/forecast), so it lives inside `_run_station`'s
    # try/except and a dead source takes down exactly one station.
    summary = {
        st.id: _run_station(st, run_date, t0, issued, out_dir, models_dir, archive_dir)
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
    statuses = {sid: result["status"] for sid, result in summary.items()}
    publish.write_scores(out_dir, [s.id for s in published], issued, statuses)
    # Same deterministic `issued`, same station set, so a same-`run_date` rerun
    # is byte-identical here too. Not called from backfill.py: its no-op guard
    # only protects the files it itself writes, so a backfill covering a station
    # that also gained a peak day would leave extremes.json stale until the
    # next daily run — an acceptable lag, extremes are not backfill's job.
    publish.write_extremes(out_dir, [s.id for s in published], issued)
    if published and not any(result["status"] == "ok" for result in summary.values()):
        raise DailyRunError(run_date, summary)
    return summary
