"""Candhis (Cerema) wave observation fetcher — getCampTR.php."""

import os
from datetime import date, timedelta

import pandas as pd
import requests

from scoreboard.config import Station
from scoreboard.sources import SourceError, make_session

_BASE_URL = "https://candhis.cerema.fr/API/v1/getCampTR.php"
_TIMEOUT = 30
_MAX_SPAN = timedelta(days=365)  # Candhis TR caps a response at ~365 days from dateDeb,
# never up to "now" — verified live: dateDeb=2021-08-06 returned 2021-08-25 -> 2022-08-05
# and nothing more recent, even though "today" was almost 5 years later.


def _fetch_chunk(
    station: Station, chunk_start: date, api_key: str, session: requests.Session
) -> pd.DataFrame:
    try:
        resp = session.get(
            _BASE_URL,
            params={"camp": station.source_id, "dateDeb": chunk_start.isoformat()},
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

    return df[(df["hs"] >= 0) & (df["hs"] < 30)].set_index("time").sort_index()


def fetch_wave_obs(
    station: Station,
    date_start: date,
    session: requests.Session | None = None,
    date_end: date | None = None,
) -> pd.DataFrame:
    """Hs/Tp, 30 min. Candhis TR caps each response at ~365 days from `dateDeb`
    (see `_MAX_SPAN`) — spans beyond that are fetched in chunks, each one
    anchored right after the previous chunk's last observation, transparent to
    the caller. A window that fits in one cap (the common case: daily/backfill
    calls a few days deep) still costs exactly one request.
    """
    api_key = os.environ.get("CANDHIS_API_KEY")
    if not api_key:
        raise SourceError(station.id, "CANDHIS_API_KEY absente de l'environnement (.env non chargé ?)")

    session = session or make_session()
    date_end = date_end or date.today()

    frames: list[pd.DataFrame] = []
    chunk_start = date_start
    while True:
        chunk = _fetch_chunk(station, chunk_start, api_key, session)
        if chunk.empty:
            break
        frames.append(chunk)
        last = chunk.index[-1].date()
        # Only chain another request if this one was actually cut short by the
        # ~365-day cap (last lands within a couple days of chunk_start + cap).
        # Short of that, `last` is the real end of available data (station lag,
        # a few hours behind "now") — a second request there would just come
        # back empty and cost a call for nothing on every daily/backfill run.
        capped = last >= chunk_start + _MAX_SPAN - timedelta(days=2)
        if last >= date_end or last <= chunk_start or not capped:
            break
        # Anchor on `last`, not `last + 1 day`: the series is 30-min, so a chunk
        # boundary mid-day would otherwise drop up to ~24h of that day's points.
        # Re-fetching `last`'s day is deliberate — `df[~df.index.duplicated]`
        # below dedupes the overlap for free, and `last <= chunk_start` above is
        # exactly the stall guard that makes this terminate: a chunk anchored on
        # its own previous end can only progress or break.
        chunk_start = last

    if not frames:
        raise SourceError(station.id, "Candhis: aucune observation exploitable")

    df = pd.concat(frames).sort_index()
    df = df[~df.index.duplicated(keep="first")]

    if df.empty:
        raise SourceError(station.id, "Candhis: aucune observation exploitable")

    return df
