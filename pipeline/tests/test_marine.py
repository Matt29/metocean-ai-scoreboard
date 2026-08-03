"""Open-Meteo marine multi-model fetcher: suffixed-key parsing, NaN-not-zero, errors."""

import json
from datetime import date
from pathlib import Path
from unittest.mock import Mock

import pytest

from scoreboard.config import Station
from scoreboard.sources import SourceError
from scoreboard.sources.marine import (
    MODEL_COLUMNS, WAVE_MODELS, fetch_wave_models_forecast, fetch_wave_models_history,
)

FIX = json.loads((Path(__file__).parent / "fixtures/marine_multi.json").read_text())
ST = Station(id="pierres-noires", name="PN", kind="wave", lat=48.29, lon=-4.97,
             source="candhis", source_id="02911", baseline="mfwam")


def make_session(payload, status=200):
    s = Mock(); r = Mock()
    r.status_code = status; r.json.return_value = payload
    s.get.return_value = r
    return s


def test_history_parses_all_models_and_sends_them():
    session = make_session(FIX)
    df = fetch_wave_models_history(ST, date(2025, 1, 1), date(2025, 1, 2), session=session)
    params = session.get.call_args.kwargs["params"]
    assert params["models"] == ",".join(WAVE_MODELS)
    assert params["start_date"] == "2025-01-01"
    assert list(df.columns) == MODEL_COLUMNS
    assert str(df.index.tz) == "UTC" and df.index.is_monotonic_increasing


def test_forecast_uses_forecast_days_not_dates():
    session = make_session(FIX)
    fetch_wave_models_forecast(ST, session=session)
    params = session.get.call_args.kwargs["params"]
    assert params["forecast_days"] == 3 and "start_date" not in params


def test_forecast_requests_past_days_for_the_error_window():
    """`last_err`/`mean_err_24h` are read backwards from t0 off this same frame:
    without `past_days` the grid starts at today 00:00 and the 24 h window is
    silently truncated to a handful of hours (train/serve skew)."""
    session = make_session(FIX)
    fetch_wave_models_forecast(ST, session=session)
    params = session.get.call_args.kwargs["params"]
    assert params["past_days"] >= 1  # >= 24 h before a 06:00 issue


def test_all_null_model_column_is_dropped_not_zero():
    # Un modèle 100% null (le piège Open-Meteo) doit donner une colonne absente
    # ou NaN, JAMAIS des zéros silencieux.
    payload = json.loads(json.dumps(FIX))
    key = next(k for k in payload["hourly"] if k != "time")
    payload["hourly"][key] = [None] * len(payload["hourly"]["time"])
    df = fetch_wave_models_history(ST, date(2025, 1, 1), date(2025, 1, 2),
                                   session=make_session(payload))
    col = "hs_" + key.removeprefix("wave_height_")
    assert df[col].isna().all()


def test_http_error_raises_source_error():
    with pytest.raises(SourceError):
        fetch_wave_models_history(ST, date(2025, 1, 1), date(2025, 1, 2),
                                  session=make_session({"reason": "boom"}, status=400))
