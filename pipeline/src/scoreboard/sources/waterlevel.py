"""SHOM REFMAR water level fetcher — /observation/json/{shom_id}."""

from datetime import date, timedelta

import pandas as pd
import requests

from scoreboard.config import Station
from scoreboard.sources import SourceError

_BASE_URL = "https://services.data.shom.fr/maregraphie/observation/json"
_TIMEOUT = 30


def fetch_tide_obs(
    station: Station, date_start: date, session: requests.Session | None = None
) -> pd.DataFrame:
    session = session or requests.Session()
    dt_start = f"{date_start.isoformat()}T00:00Z"
    dt_end = f"{(date_start + timedelta(days=1)).isoformat()}T00:00Z"
    try:
        resp = session.get(
            f"{_BASE_URL}/{station.source_id}",
            params={"sources": 1, "dtStart": dt_start, "dtEnd": dt_end},
            timeout=_TIMEOUT,
        )
        payload = resp.json()
    except (requests.RequestException, ValueError) as exc:
        raise SourceError(station.id, f"refmar request failed: {exc}") from exc

    rows = payload.get("data") or []
    if resp.status_code != 200 or not rows:
        raise SourceError(station.id, f"refmar returned no data (HTTP {resp.status_code})")

    df = pd.DataFrame(rows)[["timestamp", "value"]]
    df = df.rename(columns={"timestamp": "time", "value": "level"})
    df["time"] = pd.to_datetime(df["time"], format="%Y/%m/%d %H:%M:%S", utc=True)
    df["level"] = df["level"].astype(float)

    df = df[(df["level"] > -15) & (df["level"] < 15)]

    df = df.set_index("time").sort_index()
    df = df[~df.index.duplicated(keep="first")]
    df = df.resample("1h").mean()

    return df
