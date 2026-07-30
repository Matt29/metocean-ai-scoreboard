"""Data source fetchers — shared error type."""


class SourceError(Exception):
    """Raised when a source fetch fails; caught by the daily orchestrator to mark a station "missing"."""

    def __init__(self, station_id: str, msg: str):
        super().__init__(station_id, msg)
        self.station_id = station_id
        self.msg = msg
