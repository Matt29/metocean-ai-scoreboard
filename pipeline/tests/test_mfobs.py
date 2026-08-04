"""Obs de vent Météo-France : DPObs (temps réel) et DPClim (archive).

Les pièges couverts ici ont tous été payés au sondage du 2026-08-04, pas
imaginés : DPClim livre en **201** et **une seule fois**, et son CSV est à
virgule décimale.
"""

import time
from datetime import date
from unittest.mock import Mock

import pandas as pd
import pytest

from scoreboard.config import Station
from scoreboard.sources import SourceError
from scoreboard.sources.mfobs import fetch_wind_obs, fetch_wind_obs_archive

ST = Station(id="ouessant", name="Ouessant", kind="wind", lat=48.47, lon=-5.06,
             source="mfobs", source_id="29155005", baseline="wind-best")


@pytest.fixture(autouse=True)
def _no_real_waiting(monkeypatch):
    """Le throttle DPObs (1,5 s) et le polling DPClim (3 s) sont des cadences
    réseau : les subir en test coûtait 174 s pour 12 tests. Un test qui a besoin
    d'une cadence non nulle repatche la sienne — celui du throttle le fait."""
    monkeypatch.setattr("scoreboard.sources.mfobs._MIN_INTERVAL_S", 0)
    monkeypatch.setattr("scoreboard.sources.mfobs._POLL_SLEEP", 0)


def _resp(status, body=None, text=""):
    r = Mock()
    r.status_code = status
    r.json.return_value = body
    r.text = text
    r.content = text.encode()
    return r


# --- DPObs, temps réel --------------------------------------------------------


def test_dpobs_loops_one_request_per_hour_and_parses_ff(monkeypatch):
    """L'endpoint ne sert qu'une heure par requête : la boucle horaire est le
    contrat, pas un détail d'implémentation."""
    monkeypatch.setenv("METEOFRANCE_API_KEY", "k")
    seen = []

    def _get(url, params=None, headers=None, timeout=None):
        seen.append(params["date"])
        return _resp(200, [{"validity_time": params["date"], "ff": 7.5, "dd": 220}])

    session = Mock()
    session.get.side_effect = _get
    df = fetch_wind_obs(ST, date(2026, 8, 1), date(2026, 8, 1), session=session)

    assert len(seen) == 24, "une requête par heure de la fenêtre"
    assert len(df) == 24
    assert list(df.columns) == ["wind_speed", "wind_dir"]
    assert df["wind_speed"].eq(7.5).all()
    assert str(df.index.tz) == "UTC"


def test_dpobs_tolerates_holes_but_raises_when_nothing_came_back(monkeypatch):
    """Un trou ponctuel du réseau ne doit pas casser le run quotidien ; zéro
    heure servie, en revanche, est une panne qu'il faut voir."""
    monkeypatch.setenv("METEOFRANCE_API_KEY", "k")
    calls = {"n": 0}

    def _partial(url, params=None, headers=None, timeout=None):
        calls["n"] += 1
        if calls["n"] % 2:
            return _resp(500, text="boom")
        return _resp(200, [{"validity_time": params["date"], "ff": 6.0, "dd": 200}])

    session = Mock()
    session.get.side_effect = _partial
    df = fetch_wind_obs(ST, date(2026, 8, 1), date(2026, 8, 1), session=session)
    assert 0 < len(df) < 24

    dead = Mock()
    dead.get.side_effect = lambda *a, **k: _resp(500, text="boom")
    with pytest.raises(SourceError):
        fetch_wind_obs(ST, date(2026, 8, 1), date(2026, 8, 1), session=dead)


def test_dpobs_filters_implausible_wind(monkeypatch):
    """Un vent négatif ou à 200 m/s est un capteur en défaut, pas une tempête."""
    monkeypatch.setenv("METEOFRANCE_API_KEY", "k")
    values = iter([7.0, -3.0, 200.0] + [7.0] * 30)

    def _get(url, params=None, headers=None, timeout=None):
        return _resp(200, [{"validity_time": params["date"], "ff": next(values), "dd": 180}])

    session = Mock()
    session.get.side_effect = _get
    df = fetch_wind_obs(ST, date(2026, 8, 1), date(2026, 8, 1), session=session)
    assert df["wind_speed"].between(0, 75).all()
    assert len(df) == 22, "les deux valeurs aberrantes sont écartées, pas corrigées"


def test_dpobs_without_key_raises_before_any_request(monkeypatch):
    monkeypatch.delenv("METEOFRANCE_API_KEY", raising=False)
    session = Mock()
    with pytest.raises(SourceError, match="METEOFRANCE_API_KEY"):
        fetch_wind_obs(ST, date(2026, 8, 1), date(2026, 8, 1), session=session)
    session.get.assert_not_called()


# --- DPClim, archive ----------------------------------------------------------

CSV = (
    "POSTE;DATE;FF;DD\n"
    "29155005;2026010100;4,9;220\n"
    "29155005;2026010101;5,3;230\n"
)


def _dpclim_session(csv=CSV, deliver_status=201):
    """Reproduit le vrai comportement : 202 à la commande, puis livraison unique."""
    delivered = {"done": False}

    def _get(url, params=None, headers=None, timeout=None):
        if "commande-station" in url:
            return _resp(202, {"elaboreProduitAvecDemandeResponse": {"return": "42"}})
        if delivered["done"]:
            return _resp(410, text="production déjà livrée")
        delivered["done"] = True
        return _resp(deliver_status, text=csv)

    s = Mock()
    s.get.side_effect = _get
    s.delivered = delivered
    return s


def test_dpclim_treats_201_as_the_delivery(monkeypatch, tmp_path):
    """Le piège central : le CSV arrive en 201, pas en 200. Une boucle qui
    n'accepte que 200 jette la charge utile — et elle ne repasse jamais."""
    monkeypatch.setenv("METEOFRANCE_DPCLIM_API_KEY", "k")
    monkeypatch.setattr("scoreboard.sources.mfobs._POLL_SLEEP", 0)
    df = fetch_wind_obs_archive(
        ST, date(2026, 1, 1), date(2026, 1, 1), tmp_path, session=_dpclim_session()
    )
    assert len(df) == 2
    assert df["wind_speed"].tolist() == [4.9, 5.3], "virgule décimale décodée"
    assert df["wind_speed"].dtype.kind == "f", "sinon FF resterait du texte, silencieusement"


def test_dpclim_caches_on_disk_so_a_unique_delivery_is_never_lost(monkeypatch, tmp_path):
    """La livraison étant à usage unique, le cache disque n'est pas une
    optimisation : c'est la seule copie durable. Un second appel ne doit
    déclencher aucune requête."""
    monkeypatch.setenv("METEOFRANCE_DPCLIM_API_KEY", "k")
    monkeypatch.setattr("scoreboard.sources.mfobs._POLL_SLEEP", 0)
    session = _dpclim_session()
    fetch_wind_obs_archive(ST, date(2026, 1, 1), date(2026, 1, 1), tmp_path, session=session)
    assert list(tmp_path.glob("*.csv")), "le fichier est écrit dès la livraison"

    again = Mock()
    again.get.side_effect = AssertionError("le cache doit éviter toute requête")
    df = fetch_wind_obs_archive(
        ST, date(2026, 1, 1), date(2026, 1, 1), tmp_path, session=again
    )
    assert len(df) == 2


def test_dpclim_orders_one_year_at_a_time(monkeypatch, tmp_path):
    """Une commande couvre un an au maximum (400 au-delà) : une fenêtre de trois
    ans doit donc partir en trois commandes, pas une."""
    monkeypatch.setenv("METEOFRANCE_DPCLIM_API_KEY", "k")
    monkeypatch.setattr("scoreboard.sources.mfobs._POLL_SLEEP", 0)
    orders = []

    def _get(url, params=None, headers=None, timeout=None):
        if "commande-station" in url:
            orders.append((params["date-deb-periode"], params["date-fin-periode"]))
            return _resp(202, {"elaboreProduitAvecDemandeResponse": {"return": str(len(orders))}})
        year = 2023 + len(orders) - 1
        return _resp(201, text=f"POSTE;DATE;FF;DD\n29155005;{year}010100;4,9;220\n")

    session = Mock()
    session.get.side_effect = _get
    fetch_wind_obs_archive(ST, date(2023, 1, 1), date(2025, 12, 31), tmp_path, session=session)

    assert len(orders) == 3
    assert all(a[:4] == b[:4] for a, b in orders), "chaque commande tient dans une année civile"


def test_dpclim_without_key_raises_before_any_request(monkeypatch, tmp_path):
    monkeypatch.delenv("METEOFRANCE_DPCLIM_API_KEY", raising=False)
    session = Mock()
    with pytest.raises(SourceError, match="METEOFRANCE_DPCLIM_API_KEY"):
        fetch_wind_obs_archive(ST, date(2026, 1, 1), date(2026, 1, 1), tmp_path, session=session)
    session.get.assert_not_called()


def test_both_sources_share_one_output_convention(monkeypatch, tmp_path):
    """Mesuré le 2026-08-04 : DPObs et DPClim servent la même mesure (écart max
    0,0 m/s). Le code doit donc rendre des frames interchangeables — c'est ce qui
    autorise à entraîner sur l'archive et à scorer sur le temps réel."""
    monkeypatch.setenv("METEOFRANCE_API_KEY", "k")
    monkeypatch.setenv("METEOFRANCE_DPCLIM_API_KEY", "k")
    monkeypatch.setattr("scoreboard.sources.mfobs._POLL_SLEEP", 0)

    # Les deux mocks servent volontairement les mêmes valeurs aux mêmes heures :
    # c'est ce que la mesure du 2026-08-04 a constaté sur le réseau réel.
    by_hour = {"2026-01-01T00:00:00Z": 4.9, "2026-01-01T01:00:00Z": 5.3}
    live = Mock()
    live.get.side_effect = lambda url, params=None, headers=None, timeout=None: _resp(
        200,
        [{"validity_time": params["date"], "ff": by_hour[params["date"]], "dd": 220}]
        if params["date"] in by_hour
        else [],
    )
    a = fetch_wind_obs(ST, date(2026, 1, 1), date(2026, 1, 1), session=live)
    b = fetch_wind_obs_archive(
        ST, date(2026, 1, 1), date(2026, 1, 1), tmp_path, session=_dpclim_session()
    )
    assert list(a.columns) == list(b.columns)
    assert a.index.tz == b.index.tz
    assert a.dtypes.tolist() == b.dtypes.tolist()
    common = a.index.intersection(b.index)
    assert len(common) > 0
    assert (a.loc[common, "wind_speed"] - b.loc[common, "wind_speed"]).abs().max() == 0.0


def test_dpobs_never_requests_hours_in_the_future(monkeypatch):
    """DPObs ne peut rien servir du futur, et chaque requête compte pour le
    débit : demander la fin de la journée en cours, c'est ~22 requêtes perdues
    par station et par run — soit exactement ce qui approche du plafond."""
    monkeypatch.setenv("METEOFRANCE_API_KEY", "k")
    monkeypatch.setattr("scoreboard.sources.mfobs._MIN_INTERVAL_S", 0)
    seen = []

    def _get(url, params=None, headers=None, timeout=None):
        seen.append(pd.Timestamp(params["date"]))
        return _resp(200, [{"validity_time": params["date"], "ff": 6.0, "dd": 180}])

    session = Mock()
    session.get.side_effect = _get
    today = date.today()
    fetch_wind_obs(ST, today, today, session=session)

    assert seen, "au moins l'heure courante doit être demandée"
    assert max(seen) <= pd.Timestamp.now(tz="UTC").ceil("h")


def test_dpobs_quota_is_reported_as_such_not_as_a_missing_station(monkeypatch):
    """Mesuré le 2026-08-04 : sans throttle, les 2ᵉ et 3ᵉ stations d'un run
    recevaient zéro heure et étaient déclarées « manquantes ». Le quota est de
    notre fait et se corrige ; une station muette non. Les confondre envoie
    chercher la panne du mauvais côté."""
    monkeypatch.setenv("METEOFRANCE_API_KEY", "k")
    monkeypatch.setattr("scoreboard.sources.mfobs._MIN_INTERVAL_S", 0)
    session = Mock()
    session.get.side_effect = lambda *a, **k: _resp(429, text="Too Many Requests")

    with pytest.raises(SourceError, match="quota DPObs"):
        fetch_wind_obs(ST, date(2026, 8, 1), date(2026, 8, 1), session=session)


def test_dpobs_throttles_between_requests(monkeypatch):
    """Le throttle est le correctif, pas le message d'erreur : il doit vraiment
    espacer les appels."""
    monkeypatch.setenv("METEOFRANCE_API_KEY", "k")
    monkeypatch.setattr("scoreboard.sources.mfobs._MIN_INTERVAL_S", 0.02)
    session = Mock()
    session.get.side_effect = lambda url, params=None, headers=None, timeout=None: _resp(
        200, [{"validity_time": params["date"], "ff": 6.0, "dd": 180}]
    )
    start = time.monotonic()
    fetch_wind_obs(ST, date(2026, 8, 1), date(2026, 8, 1), session=session)
    assert time.monotonic() - start >= 0.02 * 20
