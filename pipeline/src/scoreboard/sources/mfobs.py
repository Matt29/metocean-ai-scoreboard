"""Observations de vent aux stations terrestres Météo-France — temps réel et archive.

Deux APIs pour la même mesure, et c'est mesuré, pas supposé : croisement DPObs
temps réel / archive DPClim sur 12 h communes à Ouessant-Stiff le 2026-08-04,
**écart max 0,0 m/s**, directions identiques. Une station de vent n'a donc pas
le skew train/serve que porte le forçage (ERA5 à l'entraînement, ARPEGE au
service, voir `docs/data-sources.md`) — ne pas inventer de correction pour un
biais qui n'existe pas.

* `fetch_wind_obs` — **DPObs**, le scoring quotidien. L'endpoint ne sert
  **qu'une heure par requête** (pas de plage) et le paquet toutes-stations
  répond 404 : d'où la boucle horaire ci-dessous, ~30 requêtes par station et
  par run. Les quotas mesurés (~50-60 req/min) l'absorbent largement à 3
  stations, mais c'est ce qui plafonne le nombre de stations vent publiables.
* `fetch_wind_obs_archive` — **DPClim**, l'entraînement. Commande asynchrone,
  avec trois pièges tous payés en sondage : le fichier arrive en **HTTP 201**
  (une boucle qui n'attend que 200 jette la charge utile), il n'est **livré
  qu'une fois** (`410 production déjà livrée` ensuite — d'où le cache disque
  écrit *avant* toute analyse), et une commande couvre **un an au maximum**.
  CSV à virgule décimale.

Les deux clés sont distinctes : `METEOFRANCE_DPCLIM_API_KEY` ouvre DPClim (et
DPObs), `METEOFRANCE_API_KEY` n'ouvre que DPObs. Chaque fonction demande celle
dont elle a besoin plutôt qu'une clé unique supposée tout couvrir.

Valeurs filtrées comme `candhis.fetch_wave_obs` : un vent négatif ou au-delà de
`_MAX_WIND` n'est pas une tempête, c'est un capteur en défaut.
"""

from __future__ import annotations

import logging
import os
import time
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import requests

from scoreboard.config import Station
from scoreboard.sources import SourceError, make_session

_DPOBS_URL = "https://public-api.meteofrance.fr/public/DPObs/v1/station/horaire"
_DPCLIM_URL = "https://public-api.meteofrance.fr/public/DPClim/v1/"
_TIMEOUT = 60
# Le record de vent moyen 10 min en France métropolitaine est sous 60 m/s.
_MAX_WIND = 75.0
_MAX_ORDER_DAYS = 366  # limite dure DPClim : "la période demandée ne doit pas dépasser 1 an"
_POLL_TRIES = 30
_POLL_SLEEP = 3.0
# ~40 req/min. Mesuré le 2026-08-04 : 90 requêtes d'affilée passent, ~130 non —
# la marge est volontairement large, un run quotidien n'est pas pressé et une
# station faussement « manquante » coûte une journée de scoreboard.
_MIN_INTERVAL_S = 1.5
_THROTTLE_CODES = {429, 503}

OBS_COLUMNS = ["wind_speed", "wind_dir"]

# Le cache DPClim appartient à ce module, pas à ses appelants : c'est ici qu'est
# la règle qui le rend indispensable (livraison unique, 410 ensuite). Deux
# constantes de chemin en face d'une seule ressource sur disque finiraient par
# diverger, et une année re-commandée par erreur est définitivement perdue.
OBS_CACHE_DIR = Path(__file__).resolve().parents[3] / "data_train" / "obs_wind"

log = logging.getLogger(__name__)


def _key(station: Station, name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise SourceError(station.id, f"{name} absente de l'environnement (.env non chargé ?)")
    return value


def _clean(df: pd.DataFrame, station: Station) -> pd.DataFrame:
    """Index UTC horaire dédupliqué + filtre de plausibilité. Convention unique
    des deux fetchers : ce qui sort d'ici est interchangeable, par construction."""
    df = df.set_index("time").sort_index()
    df = df[~df.index.duplicated(keep="first")]
    speed = df["wind_speed"]
    # `notna()` explicite : une comparaison seule laisserait passer les NaN en
    # False et supprimerait silencieusement les heures manquantes au lieu de les
    # laisser visibles au comptage de couverture.
    df = df[speed.isna() | ((speed >= 0) & (speed < _MAX_WIND))]
    if df.empty:
        raise SourceError(station.id, "aucune observation de vent exploitable sur la fenêtre")
    return df[OBS_COLUMNS]


def fetch_wind_obs(
    station: Station,
    date_start: date,
    date_end: date | None = None,
    session: requests.Session | None = None,
) -> pd.DataFrame:
    """Observations horaires DPObs sur [date_start, date_end] — une requête par heure.

    Une heure absente ou en erreur est simplement omise : le run quotidien doit
    survivre à un trou ponctuel du réseau, et la couverture réelle est ensuite
    jugée par `features._MIN_FORCING_COVERAGE` / le matching du scoring.

    Deux garde-fous mesurés le 2026-08-04, sur un run réel à 3 stations :

    * **Débit.** 90 requêtes enchaînées passent, ~130 non. À 3 stations d'affilée
      sans throttle, la 2ᵉ et la 3ᵉ ont reçu **zéro** heure — deux stations
      « manquantes » entièrement fabriquées par notre propre cadence. D'où
      `_MIN_INTERVAL_S`, calé sous les ~50-60 req/min annoncés.
    * **Le message d'erreur.** Un throttle ne supprime pas le risque, il le rend
      rare ; il faut donc que le cas restant se lise. Un quota épuisé et une
      station muette produisaient le même « aucune heure servie » — le premier
      est de notre fait et se corrige, le second non. Ils sont désormais
      distingués, parce qu'un diagnostic faux coûte plus cher qu'une panne.
    """
    api_key = _key(station, "METEOFRANCE_API_KEY")
    session = session or make_session()
    end = pd.Timestamp(date_end or date.today(), tz="UTC") + pd.Timedelta(hours=23)
    # Jamais au-delà de l'heure courante : DPObs ne peut rien servir du futur, et
    # ces heures-là ne sont pas gratuites — ~22 requêtes par station et par run,
    # soit exactement ce qui rapproche du plafond de débit ci-dessus.
    end = min(end, pd.Timestamp.now(tz="UTC").floor("h"))
    hours = pd.date_range(pd.Timestamp(date_start, tz="UTC"), end, freq="1h")

    rows, failures, throttled = [], 0, 0
    last_call = 0.0
    for t in hours:
        wait = _MIN_INTERVAL_S - (time.monotonic() - last_call)
        if wait > 0:
            time.sleep(wait)
        last_call = time.monotonic()
        try:
            resp = session.get(
                _DPOBS_URL,
                params={
                    "id_station": station.source_id,
                    "date": t.strftime("%Y-%m-%dT%H:00:00Z"),
                    "format": "json",
                },
                headers={"apikey": api_key},
                timeout=_TIMEOUT,
            )
            if resp.status_code != 200:
                failures += 1
                throttled += resp.status_code in _THROTTLE_CODES
                continue
            payload = resp.json()
        except (requests.RequestException, ValueError):
            failures += 1
            continue
        for item in payload or []:
            rows.append(
                {
                    "time": pd.Timestamp(item["validity_time"]),
                    "wind_speed": item.get("ff"),
                    "wind_dir": item.get("dd"),
                }
            )

    if throttled:
        # Remonté même si des heures sont passées : une fenêtre trouée par le
        # quota donne un `mean_err_24h` calculé sur autre chose que 24 h.
        raise SourceError(
            station.id,
            f"quota DPObs atteint ({throttled}/{len(hours)} requêtes refusées) — "
            "ce n'est pas un trou d'observation, c'est notre cadence",
        )
    if not rows:
        raise SourceError(
            station.id, f"DPObs n'a servi aucune heure sur [{date_start}, {end.date()}]"
        )
    if failures:
        log.info("%s: %d/%d heures DPObs non servies", station.id, failures, len(hours))
    df = pd.DataFrame(rows)
    df["time"] = pd.to_datetime(df["time"], utc=True)
    for col in OBS_COLUMNS:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return _clean(df, station)


def _order_year(station: Station, start: date, end: date, api_key: str, session) -> str:
    """Une commande DPClim -> le CSV brut. Écrit par l'appelant *avant* analyse."""
    resp = session.get(
        _DPCLIM_URL + "commande-station/horaire",
        params={
            "id-station": station.source_id,
            "date-deb-periode": f"{start.isoformat()}T00:00:00Z",
            "date-fin-periode": f"{end.isoformat()}T23:00:00Z",
        },
        headers={"apikey": api_key},
        timeout=_TIMEOUT,
    )
    if resp.status_code not in (200, 201, 202):
        raise SourceError(station.id, f"commande DPClim HTTP {resp.status_code}: {resp.text[:200]}")
    try:
        cmde = resp.json()["elaboreProduitAvecDemandeResponse"]["return"]
    except (ValueError, KeyError) as exc:
        raise SourceError(station.id, f"commande DPClim illisible: {resp.text[:200]}") from exc

    for _ in range(_POLL_TRIES):
        time.sleep(_POLL_SLEEP)
        got = session.get(
            _DPCLIM_URL + "commande/fichier",
            params={"id-cmde": cmde},
            headers={"apikey": api_key},
            timeout=_TIMEOUT * 3,
        )
        # 201 EST la livraison, pas un "en cours" — et elle est unique.
        if got.status_code in (200, 201):
            return got.content.decode("utf-8", errors="replace")
        if got.status_code not in (204, 404):
            raise SourceError(
                station.id, f"fichier DPClim HTTP {got.status_code}: {got.text[:200]}"
            )
    raise SourceError(station.id, f"commande DPClim {cmde} jamais livrée")


def fetch_wind_obs_archive(
    station: Station,
    date_start: date,
    date_end: date,
    cache_dir: Path = OBS_CACHE_DIR,
    session: requests.Session | None = None,
) -> pd.DataFrame:
    """Observations horaires archivées, une commande DPClim par année civile.

    `cache_dir` n'est pas une optimisation : la livraison DPClim étant unique, un
    fichier perdu se re-commande, mais un fichier ré-analysé sans cache ne se
    récupère pas dans le même run. On écrit donc sur disque dès la réception, et
    une année déjà présente n'est jamais recommandée.

    `date_end` est ramené à hier : DPClim refuse toute date de fin future
    (`400 la date de fin est future`), et le jour courant n'est de toute façon
    pas encore consolidé dans l'archive. Même clamp, même raison, que
    `backfill._deep_window`.
    """
    api_key = _key(station, "METEOFRANCE_DPCLIM_API_KEY")
    session = session or make_session()
    cache_dir.mkdir(parents=True, exist_ok=True)
    date_end = min(date_end, date.today() - timedelta(days=1))
    if date_start > date_end:
        raise SourceError(station.id, f"fenêtre vide après clamp: [{date_start}, {date_end}]")

    frames = []
    for year in range(date_start.year, date_end.year + 1):
        lo = max(date_start, date(year, 1, 1))
        hi = min(date_end, date(year, 12, 31))
        if lo > hi:
            continue
        if (hi - lo).days > _MAX_ORDER_DAYS:
            raise SourceError(station.id, f"fenêtre {lo}->{hi} au-delà de la limite DPClim d'un an")
        path = cache_dir / f"{station.source_id}_{lo.isoformat()}_{hi.isoformat()}.csv"
        if not path.exists():
            # Écriture atomique plutôt qu'un seuil de taille « ça a l'air complet » :
            # un tel seuil rejette un fichier légitimement court (station peu
            # émettrice, année partielle) et déclenche une **re-commande** — que
            # DPClim refuse en 410, la livraison étant unique. L'année serait alors
            # définitivement perdue. Ici un fichier qui existe est complet, par
            # construction : le rename ne devient visible qu'une fois tout écrit.
            part = path.with_suffix(".part")
            part.write_text(_order_year(station, lo, hi, api_key, session))
            part.rename(path)
        frames.append(
            pd.read_csv(
                path,
                sep=";",
                decimal=",",  # sinon FF arrive en texte et casse tout en aval, silencieusement
                usecols=["DATE", "FF", "DD"],
                low_memory=False,
            )
        )

    if not frames:
        raise SourceError(station.id, f"fenêtre vide: [{date_start}, {date_end}]")
    df = pd.concat(frames, ignore_index=True).rename(columns={"FF": "wind_speed", "DD": "wind_dir"})
    df["time"] = pd.to_datetime(df["DATE"].astype(str), format="%Y%m%d%H", utc=True)
    return _clean(df, station)
