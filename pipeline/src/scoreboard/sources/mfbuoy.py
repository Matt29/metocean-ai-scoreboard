"""Météo-France DPObs `/bouees` — observations horaires des 9 bouées ancrées.

Pourquoi archiver : l'endpoint ne sert qu'une fenêtre glissante et il n'existe
aucune API d'archive pour ces bouées. Toute heure non capturée est perdue
définitivement — et le premier entraînement Méditerranée (demande produit 4) ne
peut commencer à compter que du jour où ce collecteur tourne.

Une seule requête par run : `id_bouee` omis renvoie les 9 bouées d'un coup.
Les positions (`lat`/`lon`) viennent de la réponse elle-même, jamais en dur.

Rétention **mesurée** le 2026-08-03, pas lue dans la doc (règle du projet : la
dispo se prouve en comptant sur la requête exacte) : `date_debut` à T-96 h
répond 200 avec la grille horaire complète (97 pas distincts) ; T-120 h et
au-delà sont refusés HTTP 400 « Contrôle de date en erreur ». La doc Confluence
annonce 24 h — elle se trompe, dans le sens favorable. `LOOKBACK_HOURS` reste
sous la limite dure mesurée : c'est ce qui rend un run quotidien raté
rattrapable au lieu d'être un trou permanent.

Les valeurs sont archivées **brutes**, sans filtre de plausibilité (contrairement
à `candhis.fetch_wave_obs`) : ce corpus est la vérité terrain d'un futur
entraînement, le filtrage appartient à qui le consomme.
"""

from __future__ import annotations

import os
from datetime import timedelta

import pandas as pd
import requests

from scoreboard.sources import SourceError, make_session

_URL = "https://public-api.meteofrance.fr/public/DPObs/v1/bouees"
_TIMEOUT = 120
_SOURCE_ID = "mfbuoy"

# Sous la limite dure de 96 h mesurée, avec de la marge pour l'écart d'horloge
# entre notre `now` et celui de la passerelle (le 400 est calculé côté serveur).
LOOKBACK_HOURS = 90

# Colonnes vagues, les seules qui motivent l'archivage ; le reste du payload
# (vent, pression, températures) est conservé tel quel — il est déjà là, et il
# sert la demande « stations de vent ».
WAVE_COLUMNS = ["haut_vag", "per_moy_vag", "dir_vag"]
KEY_COLUMNS = ["geo_id_wmo", "validity_time"]


def fetch_buoy_obs(
    now: pd.Timestamp | None = None,
    session: requests.Session | None = None,
) -> pd.DataFrame:
    """Observations horaires des 9 bouées sur [now - lookback_hours, now].

    `validity_time` est renvoyé en chaîne ISO (comme `archive.write_day`) : c'est
    la moitié de la clé de déduplication, une chaîne ne dérive pas de dtype au
    passage par parquet.
    """
    api_key = os.environ.get("METEOFRANCE_API_KEY")
    if not api_key:
        raise SourceError(_SOURCE_ID, "METEOFRANCE_API_KEY absente de l'environnement (.env non chargé ?)")

    now = (now or pd.Timestamp.now(tz="UTC")).floor("h")
    start = now - timedelta(hours=LOOKBACK_HOURS)

    session = session or make_session()
    try:
        resp = session.get(
            _URL,
            params={
                "date_debut": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "date_fin": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "format": "json",
            },
            headers={"apikey": api_key},
            timeout=_TIMEOUT,
        )
    except requests.RequestException as exc:
        raise SourceError(_SOURCE_ID, f"bouees request failed: {exc}") from exc

    if resp.status_code != 200:
        # La passerelle WSO2 répond en XML sur erreur, pas en JSON — ne pas
        # tenter .json() ici, le message utile est dans le corps brut.
        raise SourceError(_SOURCE_ID, f"bouees HTTP {resp.status_code}: {resp.text[:300]}")
    try:
        rows = resp.json()
    except ValueError as exc:
        raise SourceError(_SOURCE_ID, f"bouees réponse non-JSON: {exc}") from exc

    if not rows:
        raise SourceError(_SOURCE_ID, f"bouees a répondu 200 mais 0 mesure sur [{start}, {now}]")

    obs = pd.DataFrame(rows)
    missing = [c for c in (*KEY_COLUMNS, *WAVE_COLUMNS) if c not in obs.columns]
    if missing:
        raise SourceError(_SOURCE_ID, f"colonnes absentes du payload bouees: {missing}")

    obs["validity_time"] = (
        pd.to_datetime(obs["validity_time"], utc=True, format="ISO8601")
        .dt.strftime("%Y-%m-%dT%H:%M:%S+00:00")
    )
    obs = obs.sort_values(KEY_COLUMNS)
    return obs.drop_duplicates(KEY_COLUMNS, keep="last").reset_index(drop=True)


def positions(obs: pd.DataFrame) -> list[dict]:
    """Une entrée par bouée vue dans la fenêtre : `{id, name, lat, lon, wave}`.

    Tout vient du payload, y compris les positions (règle du projet : jamais de
    coordonnée en dur). `wave` dit si la bouée a servi au moins une hauteur
    significative sur la fenêtre — c'est ce qui sépare les 8 bouées exploitables
    de BOUEE_SARDAIGNE, qui émet vent et pression mais aucune vague. Un booléen
    recalculé à chaque run plutôt qu'une liste figée : le jour où elle se remet à
    émettre, la carte le dit sans qu'on ait à y toucher.
    """
    g = obs.groupby("geo_id_wmo", sort=True)
    return [
        {
            "id": str(wmo),
            "name": str(rows["name"].iloc[0]),
            "lat": round(float(rows["lat"].iloc[0]), 4),
            "lon": round(float(rows["lon"].iloc[0]), 4),
            "wave": bool(rows["haut_vag"].notna().any()),
        }
        for wmo, rows in g
    ]


def non_null_counts(obs: pd.DataFrame) -> pd.DataFrame:
    """Non-null par bouée et par variable vagues — imprimé à chaque run.

    Un 200 OK ne prouve rien : seul le comptage sur la requête exacte prouve que
    la donnée est là (leçon payée trois fois sur ce projet). En sortie de cron,
    c'est la trace qui rend une panne silencieuse visible.
    """
    counts = obs.groupby("name")[WAVE_COLUMNS].count()
    counts.insert(0, "heures", obs.groupby("name")["validity_time"].nunique())
    return counts
