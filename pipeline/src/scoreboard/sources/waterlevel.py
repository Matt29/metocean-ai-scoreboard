"""SHOM REFMAR water level fetcher — /observation/json/{shom_id}."""

from datetime import date, timedelta

import pandas as pd
import requests

from scoreboard.config import Station
from scoreboard.sources import SourceError, make_session

_BASE_URL = "https://services.data.shom.fr/maregraphie/observation/json"
_TIMEOUT = 30
_MAX_SPAN = timedelta(days=30)  # REFMAR caps a single request at 31 days


def fetch_tide_obs(
    station: Station,
    date_start: date,
    session: requests.Session | None = None,
    date_end: date | None = None,
) -> pd.DataFrame:
    """Water level, hourly UTC. Spans over 30 days are fetched in chunks (API cap)."""
    session = session or make_session()
    date_end = date_end or date_start + timedelta(days=1)

    rows: list[dict] = []
    chunk_start = date_start
    while chunk_start < date_end:
        chunk_end = min(chunk_start + _MAX_SPAN, date_end)
        try:
            resp = session.get(
                f"{_BASE_URL}/{station.source_id}",
                params={
                    "sources": 1,
                    "dtStart": f"{chunk_start.isoformat()}T00:00Z",
                    "dtEnd": f"{chunk_end.isoformat()}T00:00Z",
                },
                timeout=_TIMEOUT,
            )
            payload = resp.json()
        except (requests.RequestException, ValueError) as exc:
            raise SourceError(station.id, f"refmar request failed: {exc}") from exc
        if resp.status_code != 200:
            raise SourceError(station.id, f"refmar returned HTTP {resp.status_code}")
        rows.extend(payload.get("data") or [])
        chunk_start = chunk_end

    if not rows:
        raise SourceError(station.id, "refmar returned no data")

    df = pd.DataFrame(rows)[["timestamp", "value"]]
    df = df.rename(columns={"timestamp": "time", "value": "level"})
    df["time"] = pd.to_datetime(df["time"], format="%Y/%m/%d %H:%M:%S", utc=True)
    df["level"] = df["level"].astype(float)

    df = df[(df["level"] > -15) & (df["level"] < 15)]

    df = df.set_index("time").sort_index()
    df = df[~df.index.duplicated(keep="first")]
    df = df.resample("1h").mean()

    return df
