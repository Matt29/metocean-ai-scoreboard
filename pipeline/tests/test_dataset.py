"""dataset.assemble: wave_models passthrough to the multi-model feature path."""

import pandas as pd

from scoreboard.config import Station
from scoreboard.dataset import assemble
from scoreboard.features import WAVE_FEATURE_COLUMNS
from scoreboard.sources.marine import MODEL_COLUMNS
from scoreboard.sources.wind import MULTI_FORCING_COLUMNS

STATION = Station(
    id="s", name="s", kind="wave", lat=48.0, lon=-5.0,
    source="candhis", source_id="1", baseline="marine-best",
)


def _hourly(cols, start, periods, value):
    idx = pd.date_range(start, periods=periods, freq="1h", tz="UTC")
    return pd.DataFrame({c: value for c in cols}, index=idx)


def _obs(start, periods, value=1.0):
    idx = pd.date_range(start, periods=periods, freq="1h", tz="UTC")
    return pd.DataFrame({"hs": value}, index=idx)


def test_assemble_wave_models_columns():
    start = pd.Timestamp("2026-01-01", tz="UTC")
    periods = 24 * 5
    obs = _obs(start, periods)
    baseline = pd.DataFrame({"hs_baseline": 1.0}, index=obs.index)
    forcing = _hourly(MULTI_FORCING_COLUMNS, start, periods, 2.0)
    waves = _hourly(MODEL_COLUMNS, start, periods, 1.5)

    x, y = assemble(STATION, obs, baseline, forcing, models=waves)

    assert list(x.columns) == WAVE_FEATURE_COLUMNS
    assert not x.empty
    assert not y.empty
    assert not x.isna().any().any()


def test_assemble_skips_issue_under_coverage_via_source_error():
    """A model entirely absent from `wave_models` -> SourceError on every issue,
    caught by the existing `except SourceError: continue` -> nothing to stack."""
    start = pd.Timestamp("2026-01-01", tz="UTC")
    periods = 24 * 5
    obs = _obs(start, periods)
    baseline = pd.DataFrame({"hs_baseline": 1.0}, index=obs.index)
    forcing = _hourly(MULTI_FORCING_COLUMNS, start, periods, 2.0)
    waves = _hourly(MODEL_COLUMNS, start, periods, 1.5)
    waves[MODEL_COLUMNS[0]] = float("nan")  # one model 100% missing

    x, y = assemble(STATION, obs, baseline, forcing, models=waves)

    assert list(x.columns) == WAVE_FEATURE_COLUMNS
    assert x.empty
    assert y.empty


def test_assemble_empty_result_typed_on_wave_columns():
    """No obs at all -> the early-return empty frame must still carry
    WAVE_FEATURE_COLUMNS when `wave_models` was passed."""
    start = pd.Timestamp("2026-01-01", tz="UTC")
    obs = pd.DataFrame({"hs": []}, index=pd.DatetimeIndex([], tz="UTC"))
    baseline = pd.DataFrame(
        {"hs_baseline": 1.0}, index=pd.date_range(start, periods=24, freq="1h", tz="UTC")
    )
    forcing = _hourly(MULTI_FORCING_COLUMNS, start, 24, 2.0)
    waves = _hourly(MODEL_COLUMNS, start, 24, 1.5)

    x, y = assemble(STATION, obs, baseline, forcing, models=waves)

    assert list(x.columns) == WAVE_FEATURE_COLUMNS
    assert x.empty
    assert y.empty
