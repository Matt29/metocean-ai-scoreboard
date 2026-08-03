"""Station config loader — stdlib tomllib, no pydantic (YAGNI)."""

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

_ENV_FILE = Path(__file__).resolve().parents[3] / ".env"


def load_env(path: Path = _ENV_FILE) -> None:
    """Charge le `.env` racine (KEY=VALUE) sans écraser l'environnement existant.

    À appeler au démarrage de chaque point d'entrée (cli, scripts/build_dataset).
    """
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))

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
