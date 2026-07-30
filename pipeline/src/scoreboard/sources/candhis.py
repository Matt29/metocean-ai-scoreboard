"""Candhis (Cerema) wave observation fetcher — getCampTR.php."""

import os
from datetime import date

import pandas as pd
import requests

from scoreboard.config import Station
from scoreboard.sources import SourceError

_BASE_URL = "https://candhis.cerema.fr/API/v1/getCampTR.php"
_TIMEOUT = 30


def fetch_wave_obs(
    station: Station, date_start: date, session: requests.Session | None = None
) -> pd.DataFrame:
    session = session or requests.Session()
    try:
        resp = session.get(
            _BASE_URL,
            params={"camp": station.source_id, "dateDeb": date_start.isoformat()},
            headers={"Authorization": os.environ.get("CANDHIS_API_KEY", "")},
            timeout=_TIMEOUT,
        )
        payload = resp.json()
    except (requests.RequestException, ValueError) as exc:
        raise SourceError(station.id, f"candhis request failed: {exc}") from exc

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
