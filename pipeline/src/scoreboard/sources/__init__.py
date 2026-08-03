"""Data source fetchers — shared error type."""

import requests
import urllib3.util.retry


def make_session() -> requests.Session:
    """Session with retry/backoff — transient 429/5xx must not cost a station's day."""
    retry = urllib3.util.retry.Retry(
        total=3,
        backoff_factor=2,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
    )
    session = requests.Session()
    adapter = requests.adapters.HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


class SourceError(Exception):
    """Raised when a source fetch fails; caught by the daily orchestrator to mark a station "missing"."""

    def __init__(self, station_id: str, msg: str):
        super().__init__(station_id, msg)
        self.station_id = station_id
        self.msg = msg
