"""`sources/mfbuoy.fetch_buoy_obs` + `archive.write_obs_days` — le collecteur
d'observations bouées Météo-France (rétention glissante, aucune archive amont).

Les faux assertent leurs arguments : la fenêtre demandée et l'auth sont
vérifiées dans le faux lui-même, pas seulement en sortie.
"""

from __future__ import annotations

import json

import pandas as pd
import pyarrow.parquet as pq
import pytest

from scoreboard import archive, archive_obs, cli
from scoreboard.sources import SourceError, mfbuoy

NOW = pd.Timestamp("2026-08-03T09:00:00Z")


def _row(name="BOUEE_AZUR", wmo="6101001", time="2026-08-03T08:00:00Z", hs=1.2):
    return {
        "lat": 43.36, "lon": 7.83, "geo_id_wmo": wmo, "name": name,
        "validity_time": time, "haut_vag": hs, "per_moy_vag": 5.0, "dir_vag": 190.0,
        "ff": 3.1, "dd": 270, "t": 295.0,
    }


class _Resp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status
        self.text = str(payload)

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class _Session:
    """Faux qui asserte : clé d'auth présente et fenêtre demandée sous la limite."""

    def __init__(self, resp):
        self.resp = resp
        self.params = None

    def get(self, url, params=None, headers=None, timeout=None):
        assert headers["apikey"], "la clé doit voyager dans le header apikey"
        assert "id_bouee" not in params, "une seule requête pour les 9 bouées"
        span = pd.Timestamp(params["date_fin"]) - pd.Timestamp(params["date_debut"])
        assert span <= pd.Timedelta(hours=96), f"fenêtre {span} au-delà de la rétention mesurée"
        self.params = params
        return self.resp


@pytest.fixture(autouse=True)
def _api_key(monkeypatch):
    monkeypatch.setenv("METEOFRANCE_API_KEY", "clé-de-test")


def test_fetch_returns_one_row_per_buoy_hour_with_iso_validity_time():
    session = _Session(_Resp([_row(), _row(name="BOUEE_LION", wmo="6101002")]))

    obs = mfbuoy.fetch_buoy_obs(now=NOW, session=session)

    assert len(obs) == 2
    assert set(obs["name"]) == {"BOUEE_AZUR", "BOUEE_LION"}
    assert obs["validity_time"].iloc[0] == "2026-08-03T08:00:00+00:00"
    assert obs["haut_vag"].notna().all()


def test_requested_window_ends_now_and_spans_lookback_hours():
    session = _Session(_Resp([_row()]))

    mfbuoy.fetch_buoy_obs(now=NOW, session=session)

    span = pd.Timestamp(session.params["date_fin"]) - pd.Timestamp(session.params["date_debut"])
    assert span == pd.Timedelta(hours=mfbuoy.LOOKBACK_HOURS)
    assert session.params["date_fin"] == "2026-08-03T09:00:00Z"


def test_missing_api_key_is_a_source_error(monkeypatch):
    monkeypatch.delenv("METEOFRANCE_API_KEY", raising=False)

    with pytest.raises(SourceError, match="METEOFRANCE_API_KEY"):
        mfbuoy.fetch_buoy_obs(now=NOW, session=_Session(_Resp([_row()])))


def test_http_error_body_is_not_parsed_as_json():
    """La passerelle WSO2 répond en XML sur 400 — .json() y lèverait un ValueError
    opaque à la place du message serveur, qui est la seule info utile."""
    session = _Session(_Resp("<am:fault>Contrôle de date en erreur</am:fault>", status=400))

    with pytest.raises(SourceError, match="Contrôle de date"):
        mfbuoy.fetch_buoy_obs(now=NOW, session=session)


def test_empty_payload_is_an_error_not_a_silent_zero_row_archive():
    with pytest.raises(SourceError, match="0 mesure"):
        mfbuoy.fetch_buoy_obs(now=NOW, session=_Session(_Resp([])))


def test_missing_wave_columns_are_an_error():
    truncated = {k: v for k, v in _row().items() if k != "haut_vag"}

    with pytest.raises(SourceError, match="haut_vag"):
        mfbuoy.fetch_buoy_obs(now=NOW, session=_Session(_Resp([truncated])))


def test_positions_flag_a_buoy_that_serves_no_wave_at_all():
    """BOUEE_SARDAIGNE émet vent et pression mais aucune vague (0 non-null mesuré
    sur 76 h) : la carte doit pouvoir la distinguer sans liste écrite à la main."""
    obs = _obs(
        _row(),
        _row(name="BOUEE_SARDAIGNE", wmo="6101035", hs=None),
        _row(name="BOUEE_SARDAIGNE", wmo="6101035", time="2026-08-03T07:00:00Z", hs=None),
    )

    by_id = {b["id"]: b for b in mfbuoy.positions(obs)}

    assert by_id["6101001"]["wave"] is True
    assert by_id["6101035"]["wave"] is False
    assert by_id["6101035"]["lat"] == 43.36  # lu dans le payload, jamais en dur


def test_non_null_counts_are_per_buoy_and_per_variable():
    obs = pd.DataFrame([
        _row(),
        _row(time="2026-08-03T07:00:00Z", hs=None),
        _row(name="BOUEE_LION", wmo="6101002"),
    ])

    counts = mfbuoy.non_null_counts(obs)

    assert counts.loc["BOUEE_AZUR", "heures"] == 2
    assert counts.loc["BOUEE_AZUR", "haut_vag"] == 1
    assert counts.loc["BOUEE_LION", "haut_vag"] == 1


# --- archivage ---------------------------------------------------------------


def _obs(*rows):
    df = pd.DataFrame(list(rows))
    df["validity_time"] = df["validity_time"].str.replace("Z", "+00:00")
    return df


def test_a_window_straddling_midnight_is_split_into_one_file_per_day(tmp_path):
    obs = _obs(_row(time="2026-08-02T23:00:00Z"), _row(time="2026-08-03T00:00:00Z"))

    written = archive.write_obs_days(tmp_path, obs, key=mfbuoy.KEY_COLUMNS)

    assert {p.name for p in written} == {"2026-08-02.parquet", "2026-08-03.parquet"}
    assert len(pq.read_table(tmp_path / "2026-08-02.parquet").to_pandas()) == 1


def test_replaying_the_same_window_does_not_duplicate(tmp_path):
    obs = _obs(_row(), _row(name="BOUEE_LION", wmo="6101002"))

    archive.write_obs_days(tmp_path, obs, key=mfbuoy.KEY_COLUMNS)
    archive.write_obs_days(tmp_path, obs, key=mfbuoy.KEY_COLUMNS)

    df = pq.read_table(tmp_path / "2026-08-03.parquet").to_pandas()
    assert len(df) == 2


def test_an_overlapping_later_run_keeps_the_hours_only_the_first_run_saw(tmp_path):
    """Le cœur de l'idempotence : la fenêtre de demain recouvre la fin d'hier.
    Un remplacement par bouée (comme `write_day`) effacerait 00h-09h d'hier."""
    archive.write_obs_days(
        tmp_path,
        _obs(_row(time="2026-08-03T01:00:00Z"), _row(time="2026-08-03T02:00:00Z")),
        key=mfbuoy.KEY_COLUMNS,
    )
    archive.write_obs_days(
        tmp_path,
        _obs(_row(time="2026-08-03T02:00:00Z"), _row(time="2026-08-03T03:00:00Z")),
        key=mfbuoy.KEY_COLUMNS,
    )

    df = pq.read_table(tmp_path / "2026-08-03.parquet").to_pandas()
    assert len(df) == 3
    assert list(df["validity_time"].str.slice(11, 13)) == ["01", "02", "03"]


def test_a_corrected_value_wins_over_the_earlier_one(tmp_path):
    """Météo-France peut re-servir une heure corrigée : la dernière écriture gagne."""
    archive.write_obs_days(tmp_path, _obs(_row(hs=1.2)), key=mfbuoy.KEY_COLUMNS)
    archive.write_obs_days(tmp_path, _obs(_row(hs=1.5)), key=mfbuoy.KEY_COLUMNS)

    df = pq.read_table(tmp_path / "2026-08-03.parquet").to_pandas()
    assert len(df) == 1
    assert df["haut_vag"].iloc[0] == 1.5


def test_a_neighbouring_day_file_is_untouched(tmp_path):
    archive.write_obs_days(tmp_path, _obs(_row(time="2026-08-02T10:00:00Z")), key=mfbuoy.KEY_COLUMNS)
    archive.write_obs_days(tmp_path, _obs(_row(time="2026-08-03T10:00:00Z")), key=mfbuoy.KEY_COLUMNS)

    assert len(pq.read_table(tmp_path / "2026-08-02.parquet").to_pandas()) == 1
    assert len(pq.read_table(tmp_path / "2026-08-03.parquet").to_pandas()) == 1


def test_empty_frame_writes_nothing(tmp_path):
    assert archive.write_obs_days(tmp_path, pd.DataFrame(), key=mfbuoy.KEY_COLUMNS) == []
    assert list(tmp_path.iterdir()) == []


# --- câblage CLI -------------------------------------------------------------


def test_run_fetches_then_archives_then_publishes(tmp_path, monkeypatch):
    monkeypatch.setattr(mfbuoy, "fetch_buoy_obs", lambda: _obs(_row()))

    obs, written = archive_obs.run(tmp_path / "archive", tmp_path / "data")

    assert len(obs) == 1
    assert [p.name for p in written] == ["2026-08-03.parquet"]
    payload = json.loads((tmp_path / "data" / "buoys.json").read_text())
    assert payload["since"] == "2026-08-03"
    assert payload["updated"] == "2026-08-03T08:00:00Z"
    assert payload["buoys"] == [
        {"id": "6101001", "name": "BOUEE_AZUR", "lat": 43.36, "lon": 7.83, "wave": True}
    ]


def test_cli_dry_run_never_touches_the_committed_archive(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(mfbuoy, "fetch_buoy_obs", lambda: _obs(_row()))
    monkeypatch.setattr(archive, "DEFAULT_OBS_ARCHIVE_DIR", tmp_path / "interdit")

    assert cli.main(["archive-obs", "--dry-run"]) == 0

    assert not (tmp_path / "interdit").exists()
    assert "BOUEE_AZUR" in capsys.readouterr().out


def test_cli_turns_a_source_outage_into_a_warning_not_a_failed_run(monkeypatch, capsys):
    """Le commit quotidien du scoreboard ne doit pas tomber avec Météo-France —
    mais la panne doit rester lisible dans le journal du cron."""
    def _boom():
        raise SourceError("mfbuoy", "bouees HTTP 503")

    monkeypatch.setattr(mfbuoy, "fetch_buoy_obs", _boom)

    assert cli.main(["archive-obs"]) == 0
    assert "::warning::archive-obs: bouees HTTP 503" in capsys.readouterr().out
