import json
from datetime import date
from pathlib import Path
from unittest.mock import Mock

import pytest

from scoreboard.config import Station
from scoreboard.sources.candhis import fetch_wave_obs
from scoreboard.sources import SourceError

FIX = json.loads((Path(__file__).parent / "fixtures/candhis_tr.json").read_text())
ST = Station(id="pierres-noires", name="PN", kind="wave", lat=48.29, lon=-4.97,
             source="candhis", source_id="02911", baseline="marine-best")


@pytest.fixture(autouse=True)
def _api_key(monkeypatch):
    monkeypatch.setenv("CANDHIS_API_KEY", "test-key")


def make_session(payload, status=200):
    s = Mock(); r = Mock()
    r.status_code = status; r.json.return_value = payload
    s.get.return_value = r
    return s


def test_parses_tr_payload():
    session = make_session(FIX)
    df = fetch_wave_obs(ST, date(2026, 7, 28), session=session)
    assert session.get.call_args.kwargs["headers"] == {"Authorization": "test-key"}
    assert list(df.columns) == ["hs", "tp"]
    assert df.index.tz is not None and str(df.index.tz) == "UTC"
    assert df["hs"].iloc[0] == 1.0          # valeur de la fixture
    assert (df["hs"] < 30).all()             # garde-fou valeurs aberrantes


def test_empty_successful_payload_raises_source_error():
    empty = {**FIX, "results": []}
    with pytest.raises(SourceError, match="aucune observation exploitable"):
        fetch_wave_obs(ST, date(2026, 7, 28), session=make_session(empty))


def test_successful_payload_with_only_filtered_values_raises_source_error():
    filtered = {**FIX, "results": [["2026-07-28 00:00", "-1", "", "8", "", "", ""]]}
    with pytest.raises(SourceError, match="aucune observation exploitable"):
        fetch_wave_obs(ST, date(2026, 7, 28), session=make_session(filtered))


def test_missing_key_raises_before_any_request(monkeypatch):
    monkeypatch.delenv("CANDHIS_API_KEY")
    session = make_session(FIX)
    with pytest.raises(SourceError, match="CANDHIS_API_KEY absente"):
        fetch_wave_obs(ST, date(2026, 7, 28), session=session)
    session.get.assert_not_called()


def test_rejected_key_raises_source_error():
    bad = {"success": False, "message": "Clé d'API non valide", "results": None}
    with pytest.raises(SourceError, match="clé Candhis refusée"):
        fetch_wave_obs(ST, date(2026, 7, 28), session=make_session(bad, status=401))


def test_failure_raises_source_error():
    bad = {"success": False, "message": "erreur interne", "results": None}
    with pytest.raises(SourceError):
        fetch_wave_obs(ST, date(2026, 7, 28), session=make_session(bad, status=500))
