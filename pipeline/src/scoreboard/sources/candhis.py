"""Candhis (Cerema) wave observation fetcher — getCampTR.php."""

import os
from datetime import date

import pandas as pd
import requests

from scoreboard.config import Station
from scoreboard.sources import SourceError, make_session

_BASE_URL = "https://candhis.cerema.fr/API/v1/getCampTR.php"
_TIMEOUT = 30


def fetch_wave_obs(
    station: Station, date_start: date, session: requests.Session | None = None
) -> pd.DataFrame:
    api_key = os.environ.get("CANDHIS_API_KEY")
    if not api_key:
        raise SourceError(station.id, "CANDHIS_API_KEY absente de l'environnement (.env non chargé ?)")

    session = session or make_session()
    try:
        resp = session.get(
            _BASE_URL,
            params={"camp": station.source_id, "dateDeb": date_start.isoformat()},
            headers={"Authorization": api_key},
            timeout=_TIMEOUT,
        )
        payload = resp.json()
    except (requests.RequestException, ValueError) as exc:
        raise SourceError(station.id, f"candhis request failed: {exc}") from exc

    if resp.status_code in (401, 403):
        raise SourceError(
            station.id,
            f"clé Candhis refusée (HTTP {resp.status_code}): {payload.get('message', '')}",
        )
    if resp.status_code != 200 or not payload.get("success"):
        raise SourceError(station.id, payload.get("message", f"HTTP {resp.status_code}"))

    entete = payload["entete"]
    results = payload["results"] or []
    df = pd.DataFrame(results, columns=entete)

    df = df.rename(columns={"Date": "time", "H1/3 (m)": "hs", "TH1/3 (s)": "tp"})
    df = df[["time", "hs", "tp"]]
    df["time"] = pd.to_datetime(df["time"], utc=True)
    df["hs"] = df["hs"].astype(float)
    df["tp"] = df["tp"].astype(float)

    df = df[(df["hs"] >= 0) & (df["hs"] < 30)]

    df = df.set_index("time").sort_index()
    df = df[~df.index.duplicated(keep="first")]

    return df
