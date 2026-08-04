"""Daily orchestration: fetch, score yesterday, infer today, publish — one station's
SourceError must never take the others down with it."""

from __future__ import annotations

import json
from datetime import date

import numpy as np
import pandas as pd
import pytest

from scoreboard import daily
from scoreboard.config import Station
from scoreboard.features import FEATURE_COLUMNS, WAVE_FEATURE_COLUMNS, WIND_FEATURE_COLUMNS
from scoreboard.sources import SourceError
from scoreboard.sources.marine import MODEL_COLUMNS
from scoreboard.sources.wind import MULTI_FORCING_COLUMNS, WIND_MODEL_COLUMNS

RUN_DATE = date(2026, 7, 30)
# Deliberately NOT the first wave model: an implementation that grabs
# `MODEL_COLUMNS[0]` instead of reading the artefact must fail these tests.
BASELINE_MODEL = "ewam"
# One distinct constant per model column, so the published `baseline` value
# alone identifies which column the serve path actually selected.
MODEL_HS = {col: 1.0 + i for i, col in enumerate(MODEL_COLUMNS)}

WAVE = Station(id="wave-a", name="Wave A", kind="wave", lat=48.0, lon=-4.0,
                source="candhis", source_id="0001", baseline="marine-best")
TIDE = Station(id="tide-b", name="Tide B", kind="tide", lat=48.4, lon=-4.5,
                source="shom", source_id="0002", baseline="harmonic")
# Deliberately NOT the first wind model, same reason as BASELINE_MODEL above.
WIND_BASELINE_MODEL = "icon_eu"
WIND = Station(id="wind-c", name="Wind C", kind="wind", lat=48.47, lon=-5.06,
                source="mfobs", source_id="29155005", baseline="wind-best")
STATIONS = [WAVE, TIDE]
GATE = {
    "wave-a": {"pass": True, "weak": False},
    "tide-b": {"pass": True, "weak": False},
}
WIND_GATE = {"wind-c": {"pass": True, "weak": False}}


class _FakePipe:
    """Predicts a constant residual — enough to exercise the plumbing.

    Carries `feature_names_in_` like every fitted sklearn estimator: that is
    what `model.predict` reorders on, so a fake without it would not exercise
    the real serving path.
    """

    def __init__(self, columns=FEATURE_COLUMNS):
        self.feature_names_in_ = np.asarray(columns)

    def predict(self, x):
        return np.zeros(len(x))


def _artifact(station_id, models_dir=None):
    """The Task 5 artefact shape: estimator + what it was fitted against.

    Wave stations serve off a named Open-Meteo model; tide keeps `None` and the
    mono-model feature list — the tide path must stay byte-for-byte as it was.
    """
    if station_id == "tide-b":
        return {"model": _FakePipe(), "baseline_model": None, "feature_columns": FEATURE_COLUMNS}
    if station_id == "wind-c":
        return {
            "model": _FakePipe(WIND_FEATURE_COLUMNS),
            "baseline_model": WIND_BASELINE_MODEL,
            "feature_columns": WIND_FEATURE_COLUMNS,
        }
    return {
        "model": _FakePipe(WAVE_FEATURE_COLUMNS),
        "baseline_model": BASELINE_MODEL,
        "feature_columns": WAVE_FEATURE_COLUMNS,
    }


def _hourly(start, periods, value):
    idx = pd.date_range(start, periods=periods, freq="1h", tz="UTC")
    return pd.Series(float(value), index=idx)


def _wave_obs_df(start, periods, value=1.3):
    idx = pd.date_range(start, periods=periods, freq="1h", tz="UTC")
    return pd.DataFrame({"hs": np.full(periods, value), "tp": np.full(periods, 8.0)}, index=idx)


def _tide_obs_df(start, date_end, value=2.0):
    """Hourly obs spanning the whole requested window — depuis les constantes
    persistées, ce n'est plus qu'`OBS_LOOKBACK_DAYS`, mais le mock reflète
    toujours la fenêtre réellement demandée plutôt qu'une durée fixe."""
    idx = pd.date_range(start, date_end, freq="1h", tz="UTC", inclusive="left")
    return pd.DataFrame({"level": np.full(len(idx), value)}, index=idx)


class _FakeHarmonic:
    """Les constantes persistées, sans utide : `daily` n'en lit que `fitted_at`
    (péremption) et `predict` (la baseline servie)."""

    def __init__(self, fitted_at):
        self.fitted_at = fitted_at

    def predict(self, times):
        return pd.Series(2.0, index=times)


def _patch_harmonic(monkeypatch, fitted_at=None):
    fitted_at = fitted_at if fitted_at is not None else pd.Timestamp(RUN_DATE, tz="UTC")
    monkeypatch.setattr(
        daily.harmonic.HarmonicModel, "load", lambda path: _FakeHarmonic(fitted_at)
    )


def _marine_df(run_date=RUN_DATE, forecast_days=3, past_days=2):
    """What `marine.fetch_wave_models_forecast` really returns: an hourly grid
    running from `past_days` before *today* 00:00 UTC through `forecast_days`
    (Open-Meteo forecast semantics — no past hours unless `past_days` asks for
    them), one Hs column per wave model."""
    idx = pd.date_range(
        pd.Timestamp(run_date, tz="UTC") - pd.Timedelta(days=past_days),
        periods=24 * (forecast_days + past_days),
        freq="1h",
    )
    return pd.DataFrame({col: np.full(len(idx), MODEL_HS[col]) for col in MODEL_COLUMNS}, index=idx)


def _wind_df(station, session=None):
    idx = pd.date_range("2026-07-25", periods=24 * 10, freq="1h", tz="UTC")
    return pd.DataFrame(
        {
            "wind_u10": np.full(len(idx), 3.0),
            "wind_v10": np.full(len(idx), -2.0),
            "pressure_anom": np.full(len(idx), 5.0),
        },
        index=idx,
    )


def _wind_models_df(station, session=None, forecast_days=3, with_speeds=False, past_days=2):
    idx = pd.date_range("2026-07-25", periods=24 * 10, freq="1h", tz="UTC")
    out = pd.DataFrame(
        {col: np.full(len(idx), 3.0 if col.startswith("wind_u10") else -2.0) for col in MULTI_FORCING_COLUMNS},
        index=idx,
    )
    if with_speeds:
        # One distinct constant per model, same trick as MODEL_HS: the published
        # `baseline` value alone tells which column the serve path selected.
        for i, col in enumerate(WIND_MODEL_COLUMNS):
            out[col] = 5.0 + i
    return out


def _wind_obs_df(start, periods=24 * 6, value=6.5):
    idx = pd.date_range(start, periods=periods, freq="1h", tz="UTC")
    return pd.DataFrame(
        {"wind_speed": np.full(periods, value), "wind_dir": np.full(periods, 220.0)}, index=idx
    )


@pytest.fixture(autouse=True)
def _archive_never_writes_into_the_repo(monkeypatch, tmp_path):
    """`daily.run` retombe sur `archive.DEFAULT_ARCHIVE_DIR` — un chemin **du
    dépôt** — quand l'appelant ne passe pas `archive_dir`. Sans ce garde-fou,
    lancer la suite dépose de vrais parquets dans `pipeline/data_forecast_archive/`
    et salit le prochain commit. Autouse : le défaut est dans la valeur par
    défaut, pas dans tel ou tel appel, donc la correction doit l'être aussi."""
    monkeypatch.setattr(daily.archive, "DEFAULT_ARCHIVE_DIR", tmp_path / "_archive")


@pytest.fixture
def patched_sources(monkeypatch):
    """Every network/model call replaced — daily.py orchestration only."""
    monkeypatch.setattr(
        daily, "fetch_wave_obs",
        lambda station, start: _wave_obs_df(start, 24 * 6),
    )
    monkeypatch.setattr(
        daily, "fetch_tide_obs",
        lambda station, start, date_end=None: _tide_obs_df(start, date_end),
    )
    monkeypatch.setattr(
        daily, "fetch_wave_models_forecast",
        lambda station, session=None, forecast_days=3, past_days=2: _marine_df(
            forecast_days=forecast_days, past_days=past_days
        ),
    )
    monkeypatch.setattr(daily, "fetch_wind_forecast", _wind_df)
    monkeypatch.setattr(daily, "fetch_wind_models_forecast", _wind_models_df)
    monkeypatch.setattr(
        daily, "fetch_wind_obs",
        lambda station, start, date_end=None: _wind_obs_df(start),
    )
    monkeypatch.setattr(daily.model, "load_artifact", _artifact)
    _patch_harmonic(monkeypatch)
    return monkeypatch


def test_first_run_publishes_latest_for_every_passing_station(tmp_path, patched_sources):
    summary = daily.run(RUN_DATE, tmp_path, stations=STATIONS, gate=GATE, archive_dir=tmp_path / "archive")

    assert summary["wave-a"]["status"] == "ok"
    assert summary["tide-b"]["status"] == "ok"
    for station_id in ["wave-a", "tide-b"]:
        payload = json.loads((tmp_path / station_id / "latest.json").read_text())
        assert payload["schema_version"] == 1
        assert len(payload["series"]) > 0
        assert "ia" in payload["series"][0] and "baseline" in payload["series"][0]

    stations_payload = json.loads((tmp_path / "stations.json").read_text())
    assert {s["id"] for s in stations_payload["stations"]} == {"wave-a", "tide-b"}


def test_a_source_error_on_one_station_does_not_block_the_others(tmp_path, patched_sources):
    def _boom(station, start):
        raise SourceError(station.id, "candhis 429")

    patched_sources.setattr(daily, "fetch_wave_obs", _boom)

    summary = daily.run(RUN_DATE, tmp_path, stations=STATIONS, gate=GATE, archive_dir=tmp_path / "archive")

    assert summary["wave-a"]["status"] == "missing"
    assert summary["tide-b"]["status"] == "ok"
    assert not (tmp_path / "wave-a" / "latest.json").exists()
    assert (tmp_path / "tide-b" / "latest.json").exists()


def test_failing_station_gets_a_missing_history_entry(tmp_path, patched_sources):
    def _boom(station, start):
        raise SourceError(station.id, "candhis 429")

    patched_sources.setattr(daily, "fetch_wave_obs", _boom)
    daily.run(RUN_DATE, tmp_path, stations=STATIONS, gate=GATE, archive_dir=tmp_path / "archive")

    history = json.loads((tmp_path / "wave-a" / "history.json").read_text())
    assert history["days"][-1] == {"date": RUN_DATE.isoformat(), "status": "missing"}


def test_second_run_scores_the_first_runs_predictions(tmp_path, patched_sources):
    daily.run(RUN_DATE, tmp_path, stations=STATIONS, gate=GATE, archive_dir=tmp_path / "archive")
    next_date = date(2026, 7, 31)

    daily.run(next_date, tmp_path, stations=STATIONS, gate=GATE, archive_dir=tmp_path / "archive")

    history = json.loads((tmp_path / "wave-a" / "history.json").read_text())
    scored_day = next(d for d in history["days"] if d["date"] == RUN_DATE.isoformat())
    assert scored_day["status"] == "ok"
    assert "mae_ia" in scored_day and "mae_baseline" in scored_day

    scores = json.loads((tmp_path / "scores.json").read_text())
    row = next(r for r in scores["stations"] if r["id"] == "wave-a")
    assert row["n_days"] == 1


def test_gate_failing_station_is_listed_but_never_gets_predictions(tmp_path, patched_sources):
    gate = {**GATE, "tide-b": {"pass": False, "weak": True}}
    summary = daily.run(RUN_DATE, tmp_path, stations=STATIONS, gate=gate, archive_dir=tmp_path / "archive")

    assert "tide-b" not in summary
    assert not (tmp_path / "tide-b").exists()
    stations_payload = json.loads((tmp_path / "stations.json").read_text())
    tide_entry = next(s for s in stations_payload["stations"] if s["id"] == "tide-b")
    assert tide_entry["published"] is False
    assert tide_entry["weak"] is True


def test_wind_source_error_marks_station_missing_without_touching_prior_latest(
    tmp_path, patched_sources
):
    daily.run(RUN_DATE, tmp_path, stations=STATIONS, gate=GATE, archive_dir=tmp_path / "archive")
    prior_bytes = (tmp_path / "wave-a" / "latest.json").read_bytes()

    def _wind_boom(station, session=None):
        raise SourceError(station.id, "open-meteo 503")

    patched_sources.setattr(daily, "fetch_wind_models_forecast", _wind_boom)
    next_date = date(2026, 7, 31)
    summary = daily.run(next_date, tmp_path, stations=STATIONS, gate=GATE, archive_dir=tmp_path / "archive")

    assert summary["wave-a"]["status"] == "missing"
    assert (tmp_path / "wave-a" / "latest.json").read_bytes() == prior_bytes


def test_missing_model_artifact_is_isolated_to_its_station(tmp_path, patched_sources):
    def _load(station_id, models_dir=None):
        if station_id == "wave-a":
            raise FileNotFoundError("no such file: wave-a.joblib")
        return _artifact(station_id, models_dir)

    patched_sources.setattr(daily.model, "load_artifact", _load)
    summary = daily.run(RUN_DATE, tmp_path, stations=STATIONS, gate=GATE, archive_dir=tmp_path / "archive")

    assert summary["wave-a"]["status"] == "missing"
    assert summary["tide-b"]["status"] == "ok"


def test_arbitrary_inference_exceptions_do_not_escape_and_block_other_stations(
    tmp_path, patched_sources
):
    """sklearn/pandas/utide can raise plain ValueError/KeyError, not just SourceError."""

    def _boom(pipe, x):
        raise ValueError("input X contains NaN")

    patched_sources.setattr(daily.model, "predict", _boom)
    summary = daily.run(RUN_DATE, tmp_path, stations=STATIONS, gate=GATE, archive_dir=tmp_path / "archive")

    assert summary["wave-a"]["status"] == "missing"
    assert summary["tide-b"]["status"] == "missing"  # both use model.predict


def test_inference_failure_still_records_a_missing_history_day(tmp_path, patched_sources):
    """Résolution 3: a degraded/unavailable forcing must be a *visible* missing day,
    not a silent gap in history.json."""

    def _wind_boom(station, session=None):
        raise SourceError(station.id, "open-meteo 503")

    patched_sources.setattr(daily, "fetch_wind_models_forecast", _wind_boom)
    daily.run(RUN_DATE, tmp_path, stations=STATIONS, gate=GATE, archive_dir=tmp_path / "archive")

    history = json.loads((tmp_path / "wave-a" / "history.json").read_text())
    assert history["days"][-1] == {"date": RUN_DATE.isoformat(), "status": "missing"}


def test_daily_run_archives_the_served_wind_forecast_for_every_published_station(
    tmp_path, patched_sources
):
    """Task A1: the wind forecast fed to the model must be kept, not thrown away,
    so a future retrain can use real ARPEGE instead of ERA5 hindsight. Task 7
    review: the served `hs_*` wave-model columns (baseline included) must be
    kept too, or the anti-skew corpus is missing 5 of the 18 wave features."""
    archive_dir = tmp_path / "archive"

    daily.run(RUN_DATE, tmp_path, stations=STATIONS, gate=GATE, archive_dir=archive_dir)

    df = pd.read_parquet(archive_dir / f"{RUN_DATE.isoformat()}.parquet")
    assert set(df["station_id"]) == {"wave-a", "tide-b"}
    # One file, two forcing shapes: the wave path archives the multi-model
    # wind frame plus the 5-model wave frame it actually served, the tide
    # path still the mono ARPEGE wind one (no wave frame at all).
    assert set(df.columns) == {
        "station_id", "issued", "valid_time", "lead_h", "source",
        "wind_u10", "wind_v10", "pressure_anom", *MULTI_FORCING_COLUMNS, *MODEL_COLUMNS,
    }
    wave, tide = df[df["station_id"] == "wave-a"], df[df["station_id"] == "tide-b"]
    assert (wave["source"] == "openmeteo:multi").all()
    assert wave[MULTI_FORCING_COLUMNS].notna().all().all()
    assert wave[MODEL_COLUMNS].notna().all().all()
    # The mono ARPEGE forcing (and its pressure anomaly) is the tide-only leg.
    assert wave[["wind_u10", "wind_v10", "pressure_anom"]].isna().all().all()
    assert (tide["source"] == "meteofrance_arpege_europe").all()
    assert tide[["wind_u10", "wind_v10", "pressure_anom"]].notna().all().all()
    assert tide[MODEL_COLUMNS].isna().all().all()
    assert df["lead_h"].min() >= 1


def test_rerunning_the_same_date_does_not_duplicate_archived_forecast_rows(
    tmp_path, patched_sources
):
    archive_dir = tmp_path / "archive"

    daily.run(RUN_DATE, tmp_path, stations=STATIONS, gate=GATE, archive_dir=archive_dir)
    n_first = len(pd.read_parquet(archive_dir / f"{RUN_DATE.isoformat()}.parquet"))

    daily.run(RUN_DATE, tmp_path, stations=STATIONS, gate=GATE, archive_dir=archive_dir)  # same date again

    df = pd.read_parquet(archive_dir / f"{RUN_DATE.isoformat()}.parquet")
    assert len(df) == n_first


def test_a_station_whose_inference_fails_is_not_archived(tmp_path, patched_sources):
    """Discriminant case: only wave-a's fetch fails, tide-b still publishes and
    archives — an implementation that never archives anything must not pass this."""
    def _wind_boom(station, session=None):
        raise SourceError(station.id, "open-meteo 503")

    # Only the wave path's forcing fails; tide's own fetch is untouched.
    patched_sources.setattr(daily, "fetch_wind_models_forecast", _wind_boom)
    archive_dir = tmp_path / "archive"

    summary = daily.run(RUN_DATE, tmp_path, stations=STATIONS, gate=GATE, archive_dir=archive_dir)

    assert summary["wave-a"]["status"] == "missing"
    assert summary["tide-b"]["status"] == "ok"
    df = pd.read_parquet(archive_dir / f"{RUN_DATE.isoformat()}.parquet")
    assert set(df["station_id"]) == {"tide-b"}


def test_archiving_failure_does_not_fail_the_run(tmp_path, patched_sources, monkeypatch, caplog):
    """The scoreboard publish must survive a broken archive write (e.g. a full
    disk, a permissions error) — visible in logs, never in the run's outcome."""
    monkeypatch.setattr(
        daily.archive, "write_day",
        lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")),
    )

    with caplog.at_level("WARNING", logger="scoreboard.daily"):
        summary = daily.run(RUN_DATE, tmp_path, stations=STATIONS, gate=GATE, archive_dir=tmp_path / "archive")

    assert summary["wave-a"]["status"] == "ok"
    assert (tmp_path / "wave-a" / "latest.json").exists()
    assert any(
        "archiving served wind forecast failed" in record.message for record in caplog.records
    )


def test_rerunning_the_same_date_does_not_invent_a_scored_day(tmp_path, patched_sources):
    """Regression for the bug the reviewer reproduced: re-running `--date` must
    never score a station's own just-published latest.json against itself."""
    daily.run(RUN_DATE, tmp_path, stations=STATIONS, gate=GATE, archive_dir=tmp_path / "archive")
    assert not (tmp_path / "wave-a" / "history.json").exists()

    daily.run(RUN_DATE, tmp_path, stations=STATIONS, gate=GATE, archive_dir=tmp_path / "archive")  # same date again

    assert not (tmp_path / "wave-a" / "history.json").exists()  # still no invented day
    scores = json.loads((tmp_path / "scores.json").read_text())
    row = next(r for r in scores["stations"] if r["id"] == "wave-a")
    assert row["n_days"] == 0


def test_rerunning_the_same_date_writes_byte_identical_scores_json(tmp_path, patched_sources):
    """Real-world regression: `scores.json["updated"]` used to be wall-clock
    `datetime.now()`, so a same-day re-run (GitHub Actions re-dispatched, or
    a schedule retry) always produced a diff and a spurious commit even when
    nothing else changed. `updated` must track `run_date`'s own issuance
    instant instead, so a same-date rerun is a true no-op on disk."""
    daily.run(RUN_DATE, tmp_path, stations=STATIONS, gate=GATE, archive_dir=tmp_path / "archive")
    first = (tmp_path / "scores.json").read_text()

    daily.run(RUN_DATE, tmp_path, stations=STATIONS, gate=GATE, archive_dir=tmp_path / "archive")
    second = (tmp_path / "scores.json").read_text()

    assert first == second


def test_scored_day_carries_n_points_and_max_lead_h(tmp_path, patched_sources):
    daily.run(RUN_DATE, tmp_path, stations=STATIONS, gate=GATE, archive_dir=tmp_path / "archive")
    daily.run(date(2026, 7, 31), tmp_path, stations=STATIONS, gate=GATE, archive_dir=tmp_path / "archive")

    history = json.loads((tmp_path / "wave-a" / "history.json").read_text())
    scored_day = next(d for d in history["days"] if d["date"] == RUN_DATE.isoformat())
    assert scored_day["n_points"] == len(scored_day["series"])
    assert scored_day["max_lead_h"] >= 1


def test_score_series_keeps_unmatched_leads_as_pending():
    """A lead with no obs yet must be kept for later verification, not dropped."""
    issued = pd.Timestamp("2026-07-30T06:00:00Z")
    series = [
        {"t": daily.iso(issued + pd.Timedelta(hours=h)), "ia": 1.1, "baseline": 1.0}
        for h in range(1, 49)
    ]
    # Covers leads 1-24 exactly, plus lead 25 via the 1h nearest-match tolerance.
    obs = _hourly(issued, periods=25, value=1.2)

    entry = daily.score_series(obs, series, issued)

    assert entry["status"] == "ok"
    assert entry["n_points"] == 25
    assert entry["max_lead_h"] == 25
    assert len(entry["pending"]) == 23
    assert all("obs" not in p for p in entry["pending"])


def _obs_capped_at_run(run_day):
    """Wave obs mock that stops at the run's own issuance instant — the real
    world: scoring yesterday's issue never sees obs beyond 'now'."""
    def _fetch(station, start):
        now = pd.Timestamp(run_day, tz="UTC") + pd.Timedelta(hours=daily.ISSUE_HOUR)
        hours = int((now - pd.Timestamp(start, tz="UTC")) / pd.Timedelta("1h"))
        return _wave_obs_df(start, hours)
    return _fetch


def test_third_run_completes_the_25_48h_leads(tmp_path, patched_sources):
    """The 25-48h half of an issue meets its obs one day after
    `_score_previous_issue` scored the 1-24h half — `_rescore_pending` must
    finish the job instead of leaving the tail forever unverified."""
    days = [RUN_DATE, date(2026, 7, 31), date(2026, 8, 1)]
    for d in days[:2]:
        patched_sources.setattr(daily, "fetch_wave_obs", _obs_capped_at_run(d))
        daily.run(d, tmp_path, stations=STATIONS, gate=GATE, archive_dir=tmp_path / "archive")

    history = json.loads((tmp_path / "wave-a" / "history.json").read_text())
    scored = next(d for d in history["days"] if d["date"] == RUN_DATE.isoformat())
    assert scored["max_lead_h"] == 24
    assert len(scored["pending"]) == 24

    patched_sources.setattr(daily, "fetch_wave_obs", _obs_capped_at_run(days[2]))
    daily.run(days[2], tmp_path, stations=STATIONS, gate=GATE, archive_dir=tmp_path / "archive")

    history = json.loads((tmp_path / "wave-a" / "history.json").read_text())
    scored = next(d for d in history["days"] if d["date"] == RUN_DATE.isoformat())
    assert scored["max_lead_h"] == 48
    assert scored["n_points"] == 48
    assert "pending" not in scored
    scores = json.loads((tmp_path / "scores.json").read_text())
    row = next(r for r in scores["stations"] if r["id"] == "wave-a")
    assert row["n_days"] == 2  # completing a day must not double-count it


def test_rescore_entry_is_a_noop_without_new_obs_and_never_downgrades():
    entry = {
        "date": "2026-07-30",
        "status": "ok",
        "series": [{"t": "2026-07-30T07:00:00Z", "obs": 1.2, "ia": 1.1, "baseline": 1.0}],
        "mae_ia": 0.1,
        "mae_baseline": 0.2,
        "n_points": 1,
        "max_lead_h": 1,
        "pending": [{"t": "2026-08-01T06:00:00Z", "ia": 1.3, "baseline": 1.4}],
    }
    # Obs that cover neither the matched point nor the pending one: strict no-op.
    far_obs = _hourly("2026-08-05", periods=4, value=9.9)
    assert daily.rescore_entry(entry, far_obs) is entry

    # Obs covering only the pending lead: the old matched point survives verbatim.
    new_obs = _hourly("2026-08-01T06:00:00Z", periods=2, value=1.5)
    rescored = daily.rescore_entry(entry, new_obs)
    assert rescored["n_points"] == 2
    assert rescored["max_lead_h"] == 48
    assert "pending" not in rescored
    assert rescored["series"][0] == entry["series"][0]


def test_stale_pending_is_dropped_after_max_age(tmp_path, patched_sources):
    """Leads whose obs window has passed for good must stop being carried."""
    from datetime import timedelta

    from scoreboard import publish

    old_day = RUN_DATE - timedelta(days=daily.PENDING_MAX_AGE_DAYS + 1)
    entry = {
        "date": old_day.isoformat(),
        "status": "ok",
        "series": [{"t": f"{old_day}T07:00:00Z", "obs": 1.2, "ia": 1.1, "baseline": 1.0}],
        "mae_ia": 0.1,
        "mae_baseline": 0.2,
        "n_points": 1,
        "max_lead_h": 1,
        "pending": [{"t": f"{old_day}T20:00:00Z", "ia": 1.3, "baseline": 1.4}],
    }
    publish.upsert_history(tmp_path, "wave-a", entry)

    daily.run(RUN_DATE, tmp_path, stations=STATIONS, gate=GATE, archive_dir=tmp_path / "archive")

    history = json.loads((tmp_path / "wave-a" / "history.json").read_text())
    stale = next(d for d in history["days"] if d["date"] == entry["date"])
    assert "pending" not in stale
    assert stale["series"] == entry["series"]  # scored points untouched


def test_expired_harmonic_constants_mark_the_station_missing(tmp_path, patched_sources):
    """Un cron de ré-ajustement mort doit faire une station manquante, pas une
    baseline périmée servie en silence. La péremption est `harmonic.REFIT_DAYS`,
    la cadence même que le backtest rejoue : au-delà, la production servirait une
    baseline plus vieille que celle sur laquelle le modèle a été noté."""
    t0 = pd.Timestamp(RUN_DATE, tz="UTC") + pd.Timedelta(hours=daily.ISSUE_HOUR)
    fresh = t0 - pd.Timedelta(days=daily.harmonic.REFIT_DAYS)
    _patch_harmonic(patched_sources, fitted_at=fresh)
    assert daily.run(
        RUN_DATE, tmp_path, stations=STATIONS, gate=GATE, archive_dir=tmp_path / "archive"
    )["tide-b"]["status"] == "ok"

    _patch_harmonic(patched_sources, fitted_at=fresh - pd.Timedelta(days=1))
    summary = daily.run(
        RUN_DATE, tmp_path, stations=STATIONS, gate=GATE, archive_dir=tmp_path / "archive"
    )
    assert summary["tide-b"]["status"] == "missing"
    assert "harmonique" in summary["tide-b"]["reason"]


def test_the_daily_tide_fetch_no_longer_asks_for_the_fit_window(tmp_path, patched_sources):
    """Le gain de la persistance : ~4 jours de REFMAR par run au lieu de deux ans
    (~50 requêtes, ~160 Mo). Une régression ici est silencieuse — le run reste
    vert, il coûte juste 50 s de plus chaque matin."""
    seen = {}

    def _tide(station, start, date_end=None):
        seen["span"] = (date_end - start).days
        return _tide_obs_df(start, date_end)

    patched_sources.setattr(daily, "fetch_tide_obs", _tide)
    daily.run(RUN_DATE, tmp_path, stations=STATIONS, gate=GATE, archive_dir=tmp_path / "archive")

    assert seen["span"] <= daily.OBS_LOOKBACK_DAYS + 1  # +1 : `date_end` = demain


# --- Task 6: the multi-model serve path ------------------------------------


def test_wave_run_serves_the_multi_model_sources_and_never_mfwam(tmp_path, patched_sources):
    """The wave path must fetch Open-Meteo marine + the 3-model wind, pick its
    baseline column from the artefact, and leave the tide path on its mono
    ARPEGE forcing. MFWAM/CMEMS must not be reachable from daily.py at all."""
    seen: dict[str, list[str]] = {}

    def _marine(station, session=None, forecast_days=3, past_days=2):
        assert station.kind == "wave", "marine must never be fetched for a tide station"
        assert forecast_days >= 3, "the +48h horizon needs at least 3 forecast days"
        assert past_days >= 1, "the 24h error window needs past hours (see test below)"
        seen.setdefault("marine", []).append(station.id)
        return _marine_df(past_days=past_days)

    def _multi_wind(station, session=None):
        seen.setdefault("multi_wind", []).append(station.id)
        return _wind_models_df(station)

    def _mono_wind(station, session=None):
        seen.setdefault("mono_wind", []).append(station.id)
        return _wind_df(station)

    patched_sources.setattr(daily, "fetch_wave_models_forecast", _marine)
    patched_sources.setattr(daily, "fetch_wind_models_forecast", _multi_wind)
    patched_sources.setattr(daily, "fetch_wind_forecast", _mono_wind)

    summary = daily.run(RUN_DATE, tmp_path, stations=STATIONS, gate=GATE, archive_dir=tmp_path / "archive")

    assert summary["wave-a"]["status"] == "ok"
    assert summary["tide-b"]["status"] == "ok"
    assert seen["marine"] == ["wave-a"]
    assert seen["multi_wind"] == ["wave-a"]
    assert seen["mono_wind"] == ["tide-b"]  # tide forcing strictly unchanged
    assert not hasattr(daily, "fetch_wave_forecast")  # CMEMS/MFWAM out of the serve path

    # The served baseline is the artefact's column, not the first one available.
    payload = json.loads((tmp_path / "wave-a" / "latest.json").read_text())
    assert {p["baseline"] for p in payload["series"]} == {MODEL_HS[f"hs_{BASELINE_MODEL}"]}


def test_latest_json_carries_the_baseline_model_from_the_artefact(tmp_path, patched_sources):
    """Additive key, same `schema_version`: the live site reads the old keys."""
    daily.run(RUN_DATE, tmp_path, stations=STATIONS, gate=GATE, archive_dir=tmp_path / "archive")

    wave = json.loads((tmp_path / "wave-a" / "latest.json").read_text())
    assert wave["baseline_model"] == BASELINE_MODEL
    assert wave["schema_version"] == 1
    assert "series" in wave and "issued" in wave and "station" in wave

    tide = json.loads((tmp_path / "tide-b" / "latest.json").read_text())
    assert "baseline_model" not in tide  # harmonic baseline has no Open-Meteo model


def test_scored_history_entry_carries_the_baseline_model(tmp_path, patched_sources):
    daily.run(RUN_DATE, tmp_path, stations=STATIONS, gate=GATE, archive_dir=tmp_path / "archive")
    daily.run(date(2026, 7, 31), tmp_path, stations=STATIONS, gate=GATE, archive_dir=tmp_path / "archive")

    history = json.loads((tmp_path / "wave-a" / "history.json").read_text())
    scored = next(d for d in history["days"] if d["date"] == RUN_DATE.isoformat())
    assert scored["baseline_model"] == BASELINE_MODEL

    tide_history = json.loads((tmp_path / "tide-b" / "history.json").read_text())
    tide_day = next(d for d in tide_history["days"] if d["date"] == RUN_DATE.isoformat())
    assert "baseline_model" not in tide_day


def test_artefact_feature_columns_mismatch_marks_only_that_station_missing(tmp_path, patched_sources):
    """A stale artefact (fitted on another column list) must refuse to predict
    rather than serve a silently subset/reordered feature frame — and only that
    station goes missing."""

    def _load(station_id, models_dir=None):
        art = _artifact(station_id, models_dir)
        if station_id == "wave-a":
            stale = WAVE_FEATURE_COLUMNS[:-1]
            art["feature_columns"], art["model"] = stale, _FakePipe(stale)
        return art

    patched_sources.setattr(daily.model, "load_artifact", _load)
    summary = daily.run(RUN_DATE, tmp_path, stations=STATIONS, gate=GATE, archive_dir=tmp_path / "archive")

    assert summary["wave-a"]["status"] == "missing"
    assert "feature" in summary["wave-a"]["reason"]
    assert not (tmp_path / "wave-a" / "latest.json").exists()
    assert summary["tide-b"]["status"] == "ok"


def test_a_pre_switch_latest_json_without_baseline_model_still_scores(tmp_path, patched_sources):
    """Scoring continuity across the switch: yesterday's issue was published by
    the MFWAM-era code and has no `baseline_model` key. It must be read back and
    scored normally, and nothing must be invented for it."""
    t0 = pd.Timestamp(RUN_DATE, tz="UTC") + pd.Timedelta(hours=daily.ISSUE_HOUR)
    old = {
        "schema_version": 1,
        "station": "wave-a",
        "issued": daily.iso(t0),
        "series": [
            {"t": daily.iso(t0 + pd.Timedelta(hours=h)), "ia": 1.1, "baseline": 1.0}
            for h in range(1, 25)
        ],
    }
    (tmp_path / "wave-a").mkdir(parents=True)
    (tmp_path / "wave-a" / "latest.json").write_text(json.dumps(old))

    daily.run(date(2026, 7, 31), tmp_path, stations=STATIONS, gate=GATE, archive_dir=tmp_path / "archive")

    history = json.loads((tmp_path / "wave-a" / "history.json").read_text())
    scored = next(d for d in history["days"] if d["date"] == RUN_DATE.isoformat())
    assert scored["status"] == "ok"
    assert scored["n_points"] == 24
    assert "baseline_model" not in scored  # unknown for a pre-switch issue, never guessed


def test_serve_baseline_covers_the_full_24h_error_window(patched_sources):
    """The property the switch first lost: `build_features` reads the baseline
    *backwards* from t0 for `last_err`/`mean_err_24h`, so the marine frame must
    carry the whole 24 h before the issue. With Open-Meteo's default (grid starts
    today 00:00) only ~6 h are covered before a 06:00 issue, and the mean is
    silently computed on those — while training averages over the full window."""
    t0 = pd.Timestamp(RUN_DATE, tz="UTC") + pd.Timedelta(hours=daily.ISSUE_HOUR)
    baseline = daily._baseline_window(WAVE, t0, _marine_df(), BASELINE_MODEL)

    past = baseline[baseline.index <= t0]
    assert len(past) == 24  # the full window `_baseline_window` clips to
    assert past.index[0] == t0 - pd.Timedelta(hours=23)
    # and the +48h horizon is untouched by the past hours
    assert baseline[baseline.index > t0].index[-1] == t0 + pd.Timedelta(hours=48)


def test_past_hours_do_not_add_leads_to_the_issued_series(tmp_path, patched_sources):
    """`past_days` feeds the error window only — the published series must stay
    the 48 future leads, never rows in the past."""
    daily.run(RUN_DATE, tmp_path, stations=STATIONS, gate=GATE, archive_dir=tmp_path / "archive")

    payload = json.loads((tmp_path / "wave-a" / "latest.json").read_text())
    assert len(payload["series"]) == 48
    assert all(p["t"] > payload["issued"] for p in payload["series"])


# --- stations de vent (kind="wind", demande produit 3) ------------------------
# Elles tournent sur leur propre `stations`/`gate` plutôt que dans STATIONS : le
# chemin vent doit être vérifié sans déplacer d'un pouce ce que les tests houle
# et marée mesurent déjà.


def test_wind_run_publishes_off_the_artefact_baseline_model(tmp_path, patched_sources):
    """La baseline servie est la colonne `ws_<baseline_model>` de l'artefact —
    pas le premier modèle venu. Chaque modèle porte une constante distincte, donc
    la valeur publiée suffit à trancher laquelle a été prise."""
    daily.run(RUN_DATE, tmp_path, stations=[WIND], gate=WIND_GATE)

    latest = json.loads((tmp_path / "wind-c" / "latest.json").read_text())
    assert latest["baseline_model"] == WIND_BASELINE_MODEL
    expected = 5.0 + WIND_MODEL_COLUMNS.index(f"ws_{WIND_BASELINE_MODEL}")
    assert {p["baseline"] for p in latest["series"]} == {expected}
    assert latest["series"], "une station vent qui passe le gate doit publier une série"


def test_wind_run_fetches_speeds_and_forcing_in_a_single_request(tmp_path, patched_sources):
    """Vitesses (baseline) et u/v (forçage) sortent du même payload Open-Meteo :
    le chemin vent doit donc appeler `fetch_wind_models_forecast` **une seule
    fois**, avec `with_speeds=True`. Deux appels = une requête payée pour rien."""
    calls = []

    def _spy(station, session=None, forecast_days=3, with_speeds=False, past_days=2):
        calls.append(with_speeds)
        return _wind_models_df(station, with_speeds=with_speeds)

    patched_sources.setattr(daily, "fetch_wind_models_forecast", _spy)
    daily.run(RUN_DATE, tmp_path, stations=[WIND], gate=WIND_GATE)

    assert calls == [True]


def test_wind_station_never_touches_the_wave_or_tide_sources(tmp_path, patched_sources):
    """Une station vent ne doit ni appeler la marine, ni les obs houle/marée."""
    def _boom(*args, **kwargs):
        raise AssertionError("source hors du chemin vent appelée")

    for name in ("fetch_wave_models_forecast", "fetch_wave_obs", "fetch_tide_obs",
                 "fetch_wind_forecast"):
        patched_sources.setattr(daily, name, _boom)

    summary = daily.run(RUN_DATE, tmp_path, stations=[WIND], gate=WIND_GATE)
    assert summary["wind-c"]["status"] == "ok"


def test_wind_obs_failure_marks_only_that_station_missing(tmp_path, patched_sources):
    """Même contrat que les autres kinds (résolution 5) : DPObs muet = station
    manquante du jour, jamais une exception qui remonte."""
    def _fail(station, start, date_end=None):
        raise SourceError(station.id, "DPObs n'a servi aucune heure")

    patched_sources.setattr(daily, "fetch_wind_obs", _fail)
    summary = daily.run(RUN_DATE, tmp_path, stations=[WIND], gate=WIND_GATE)

    assert summary["wind-c"]["status"] == "missing"
    history = json.loads((tmp_path / "wind-c" / "history.json").read_text())
    assert history["days"][-1]["status"] == "missing"


def test_wind_second_run_scores_the_first_runs_predictions(tmp_path, patched_sources):
    """Le vent hérite du scoring quotidien sans code dédié : la série émise la
    veille est relue et confrontée aux obs du jour."""
    daily.run(RUN_DATE, tmp_path, stations=[WIND], gate=WIND_GATE)
    daily.run(date(2026, 7, 31), tmp_path, stations=[WIND], gate=WIND_GATE)

    history = json.loads((tmp_path / "wind-c" / "history.json").read_text())
    scored = [d for d in history["days"] if d["date"] == RUN_DATE.isoformat()]
    assert scored and scored[0]["status"] == "ok"
    assert scored[0]["n_points"] > 0
    assert scored[0]["baseline_model"] == WIND_BASELINE_MODEL


def test_unknown_obs_source_raises_instead_of_falling_through_to_shom():
    """Le dispatch d'obs porte sur `source`, pas sur `kind`.

    Une source sans collecteur — la prochaine sera `mfbuoy`, de la houle qui ne
    vient pas de Candhis — doit lever, pas atterrir chez le collecteur du kind.
    Un mauvais aiguillage publierait un jour *faux*, pas un jour manquant.
    """
    orphan = Station(id="orphan", name="Orphan", kind="wave", lat=48.0, lon=-4.0,
                     source="mfbuoy", source_id="0003", baseline="marine-best")
    with pytest.raises(SourceError, match="mfbuoy"):
        daily._fetch_obs(orphan, RUN_DATE)
