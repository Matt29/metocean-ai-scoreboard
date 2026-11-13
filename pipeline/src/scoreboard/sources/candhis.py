"""Candhis (Cerema) wave observation fetcher — getCampTR.php."""

import os
from datetime import date, timedelta

import pandas as pd
import requests

from scoreboard.config import Station
from scoreboard.sources import SourceError, make_session

_BASE_URL = "https://candhis.cerema.fr/API/v1/getCampTR.php"
_TIMEOUT = 30
_CAP_SLACK = timedelta(days=2)  # a capped response ends within ~2 days of dateDeb + 365 d
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

    results = payload["results"] or []
    if not results:
        # "Pas de données pour la campagne…" comes back as success with
        # `entete: null` — the columns below would not exist (seen 2026-09-14,
        # Candhis silent on every station since 2026-09-08 12:00).
        return pd.DataFrame(columns=["hs", "tp"], index=pd.DatetimeIndex([], tz="UTC", name="time"))
    df = pd.DataFrame(results, columns=payload["entete"])

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
    anchored on the previous chunk's last observation (or past a window the buoy
    left empty), transparent to the caller. A window that fits in one cap (the common case: daily/backfill
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
        window_end = chunk_start + _MAX_SPAN - _CAP_SLACK
        if chunk.empty:
            last = chunk_start
        else:
            frames.append(chunk)
            last = chunk.index[-1].date()
        # One rule for both ways a response stops short of `date_end`. Cut by the
        # cap: resume ON `last`, not `last + 1 day` — the series is 30-min, a
        # mid-day boundary would drop the rest of that day (the overlap is
        # deduped below). Silent buoy inside the window (Anglet, 2025-04-22 ->
        # 2025-11-18), or empty window: skip to the end of what this request
        # could cover. `window_end > chunk_start`, so the loop always progresses,
        # and a window shorter than the cap stops after its single request.
        chunk_start = max(last, window_end)
        if chunk_start >= date_end:
            break

    if not frames:
        raise SourceError(station.id, "Candhis: aucune observation exploitable")

    df = pd.concat(frames).sort_index()
    df = df[~df.index.duplicated(keep="first")]

    if df.empty:
        raise SourceError(station.id, "Candhis: aucune observation exploitable")

    return df
