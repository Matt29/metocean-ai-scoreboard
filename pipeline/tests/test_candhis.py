import json
from datetime import date
from pathlib import Path
from unittest.mock import Mock

from scoreboard.config import Station
from scoreboard.sources.candhis import fetch_wave_obs
from scoreboard.sources import SourceError

FIX = json.loads((Path(__file__).parent / "fixtures/candhis_tr.json").read_text())
ST = Station(id="pierres-noires", name="PN", kind="wave", lat=48.29, lon=-4.97,
             source="candhis", source_id="02911", baseline="mfwam")


def make_session(payload, status=200):
    s = Mock(); r = Mock()
    r.status_code = status; r.json.return_value = payload
    s.get.return_value = r
    return s


def test_parses_tr_payload():
    df = fetch_wave_obs(ST, date(2026, 7, 28), session=make_session(FIX))
    assert list(df.columns) == ["hs", "tp"]
    assert df.index.tz is not None and str(df.index.tz) == "UTC"
    assert df["hs"].iloc[0] == 1.0          # valeur de la fixture
    assert (df["hs"] < 30).all()             # garde-fou valeurs aberrantes


def test_failure_raises_source_error():
    bad = {"success": False, "message": "Clé d'API non valide", "results": None}
    import pytest
    with pytest.raises(SourceError):
        fetch_wave_obs(ST, date(2026, 7, 28), session=make_session(bad, status=401))
