from collections import Counter
from pathlib import Path
from scoreboard.config import load_stations, Station


def test_load_stations_parses_toml(tmp_path: Path):
    f = tmp_path / "stations.toml"
    f.write_text("""
[[station]]
id = "pierres-noires"
name = "Les Pierres Noires"
kind = "wave"
lat = 48.29
lon = -4.97
source = "candhis"
source_id = "02911"
baseline = "marine-best"
""")
    stations = load_stations(f)
    assert stations == [
        Station(
            id="pierres-noires",
            name="Les Pierres Noires",
            kind="wave",
            lat=48.29,
            lon=-4.97,
            source="candhis",
            source_id="02911",
            baseline="marine-best",
        )
    ]


def test_load_stations_rejects_bad_kind(tmp_path: Path):
    f = tmp_path / "stations.toml"
    f.write_text(
        '[[station]]\nid="x"\nname="x"\nkind="banana"\nlat=0.0\nlon=0.0\nsource="candhis"\nsource_id="0"\nbaseline="marine-best"\n'
    )
    import pytest

    with pytest.raises(ValueError):
        load_stations(f)


def test_load_stations_default_loads_real_config():
    """Guards config drift: the real pipeline/config/stations.toml must always
    parse, and every station must declare a kind/source/baseline the loader
    accepts. The count is asserted per kind rather than as one total, so adding
    a station of one kind cannot silently mask the loss of another."""
    stations = load_stations()
    kinds = Counter(s.kind for s in stations)
    assert kinds == {"wave": 4, "tide": 2, "wind": 3}
    assert len({s.id for s in stations}) == len(stations)
    assert all(s.source_id for s in stations)


def test_inactive_pilot_is_explicitly_opted_into_from_real_config():
    active = load_stations()
    with_pilots = load_stations(include_inactive=True)

    assert "gascogne-bouee" not in {s.id for s in active}
    gascogne = next(s for s in with_pilots if s.id == "gascogne-bouee")
    assert gascogne.source == "mfbuoy"
    assert gascogne.source_id == "6200001"
    assert gascogne.active is False
