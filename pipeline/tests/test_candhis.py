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


def _row_payload(date_str: str) -> dict:
    return {
        "success": True,
        "entete": [
            "Date", "H1/3 (m)", "Hmax (m)", "TH1/3 (s)",
            "Dir. au pic (°)", "Etal. au pic (°)", "Temp. mer (°C)",
        ],
        "results": [[date_str, "1.0000", "1.6000", "8.6000", "295.0000", "23.0000", "18.6000"]],
    }


def test_short_window_is_a_single_request():
    """A daily/backfill-sized window never hits the ~365-day cap: one call."""
    session = make_session(FIX)
    df = fetch_wave_obs(ST, date(2026, 7, 28), session=session)  # default date_end = today
    assert session.get.call_count == 1
    assert not df.empty


def test_chains_requests_when_window_exceeds_cap():
    """A response cut off close to the ~365-day cap triggers a second, chained
    request picking up right after the first one's last observation."""
    first_start = date(2020, 1, 1)
    last1 = "2020-12-30 00:00"   # within 2 days of first_start + 365d -> "capped"
    last2 = "2021-06-01 00:00"   # past date_end -> chaining stops

    session = Mock()
    session.get.side_effect = [
        Mock(status_code=200, json=Mock(return_value=_row_payload(last1))),
        Mock(status_code=200, json=Mock(return_value=_row_payload(last2))),
    ]

    df = fetch_wave_obs(ST, first_start, session=session, date_end=date(2021, 3, 1))

    assert session.get.call_count == 2
    first_call, second_call = session.get.call_args_list
    assert first_call.kwargs["params"]["dateDeb"] == "2020-01-01"
    # Anchored on last1 itself, not last1 + 1 day: a mid-day boundary must not
    # skip the rest of that day (résolution 5, see candhis.py).
    assert second_call.kwargs["params"]["dateDeb"] == "2020-12-30"
    assert len(df) == 2
    assert list(df.index.date) == [date(2020, 12, 30), date(2021, 6, 1)]


def test_chunk_join_does_not_drop_same_day_points():
    """A chunk cut off mid-day must not lose that day's remaining points: the
    next chunk re-requests `last`'s own day, and dedup on the index (not on a
    day skip) is what keeps the joined series whole."""
    first_start = date(2020, 1, 1)
    boundary_day = "2020-12-30"

    def payload_two_points_same_day(hour1: str, hour2: str) -> dict:
        return {
            "success": True,
            "entete": [
                "Date", "H1/3 (m)", "Hmax (m)", "TH1/3 (s)",
                "Dir. au pic (°)", "Etal. au pic (°)", "Temp. mer (°C)",
            ],
            "results": [
                [f"{boundary_day} {hour1}", "1.0000", "1.6000", "8.6000", "295.0000", "23.0000", "18.6000"],
                [f"{boundary_day} {hour2}", "1.1000", "1.6000", "8.6000", "295.0000", "23.0000", "18.6000"],
            ],
        }

    session = Mock()
    session.get.side_effect = [
        # First chunk stops mid-day, within the ~365-day cap window -> "capped".
        Mock(status_code=200, json=Mock(return_value=payload_two_points_same_day("00:00", "12:00"))),
        # Second chunk, re-anchored on that same day, has the rest of it plus a
        # point past date_end -> chaining stops here.
        Mock(status_code=200, json=Mock(return_value=_row_payload("2021-06-01 00:00"))),
    ]

    df = fetch_wave_obs(ST, first_start, session=session, date_end=date(2021, 3, 1))

    assert session.get.call_count == 2
    # Both boundary-day points survive the join — nothing lost, nothing duplicated.
    assert len(df[df.index.date == date(2020, 12, 30)]) == 2


def test_chunk_loop_terminates_when_second_chunk_makes_no_progress():
    """If the re-anchored chunk still ends on the exact same day (no more data
    yet available past it), the stall guard must break — not loop forever."""
    first_start = date(2020, 1, 1)
    same_day = "2020-12-30 12:00"  # within cap of first_start -> "capped" on chunk 1

    session = Mock()
    session.get.side_effect = [
        Mock(status_code=200, json=Mock(return_value=_row_payload(same_day))),
        # Re-anchored dateDeb == 2020-12-30: this response returns the same day
        # again, so `last <= chunk_start` must stop the loop right here.
        Mock(status_code=200, json=Mock(return_value=_row_payload(same_day))),
    ]

    df = fetch_wave_obs(ST, first_start, session=session, date_end=date(2021, 3, 1))

    assert session.get.call_count == 2  # not 3+: the loop terminated
    assert len(df) == 1
