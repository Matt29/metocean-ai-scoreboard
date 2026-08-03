from pathlib import Path
from scoreboard.config import load_stations, Station


def test_load_stations_parses_toml(tmp_path: Path):
    f = tmp_path / "stations.toml"
    f.write_text('''
[[station]]
id = "pierres-noires"
name = "Les Pierres Noires"
kind = "wave"
lat = 48.29
lon = -4.97
source = "candhis"
source_id = "02911"
baseline = "marine-best"
''')
    stations = load_stations(f)
    assert stations == [Station(id="pierres-noires", name="Les Pierres Noires",
                                kind="wave", lat=48.29, lon=-4.97,
                                source="candhis", source_id="02911", baseline="marine-best")]

def test_load_stations_rejects_bad_kind(tmp_path: Path):
    f = tmp_path / "stations.toml"
    f.write_text('[[station]]\nid="x"\nname="x"\nkind="banana"\nlat=0.0\nlon=0.0\nsource="candhis"\nsource_id="0"\nbaseline="marine-best"\n')
    import pytest
    with pytest.raises(ValueError):
        load_stations(f)


def test_load_stations_default_loads_real_config():
    """Guards config drift: the real pipeline/config/stations.toml must always
    parse and match the 6 stations decided in Task 1."""
    stations = load_stations()
    assert len(stations) == 6
