"""Station config loader — stdlib tomllib, no pydantic (YAGNI)."""

import tomllib
from dataclasses import dataclass
from pathlib import Path

_VALID_KINDS = {"wave", "tide"}
_VALID_SOURCES = {"candhis", "shom", "ioc"}
_VALID_BASELINES = {"mfwam", "harmonic"}

_DEFAULT_PATH = Path(__file__).resolve().parents[2] / "config" / "stations.toml"


@dataclass(frozen=True)
class Station:
    id: str
    name: str
    kind: str
    lat: float
    lon: float
    source: str
    source_id: str
    baseline: str


def load_stations(path: Path | None = None) -> list[Station]:
    if path is None:
        path = _DEFAULT_PATH
    with path.open("rb") as f:
        data = tomllib.load(f)

    stations = []
    for raw in data.get("station", []):
        if raw["kind"] not in _VALID_KINDS:
            raise ValueError(f"invalid kind: {raw['kind']!r}")
        if raw["source"] not in _VALID_SOURCES:
            raise ValueError(f"invalid source: {raw['source']!r}")
        if raw["baseline"] not in _VALID_BASELINES:
            raise ValueError(f"invalid baseline: {raw['baseline']!r}")
        stations.append(Station(**raw))
    return stations
