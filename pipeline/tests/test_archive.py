"""`archive.write_day` — the served wind forecast, kept for an honest future
retrain (see `docs/data-sources.md`, ERA5-train/ARPEGE-serve skew)."""

from __future__ import annotations

import pandas as pd
import pyarrow.parquet as pq

from scoreboard import archive

ISSUED = pd.Timestamp("2026-07-30T06:00:00Z")


def _forcing(start="2026-07-30T06:00:00Z", periods=4):
    idx = pd.date_range(start, periods=periods, freq="1h", tz="UTC")
    return pd.DataFrame({"wind_u10": [1.0, 2.0, 3.0, 4.0][:periods], "wind_v10": [-1.0, -2.0, -3.0, -4.0][:periods]}, index=idx)


def _times(start="2026-07-30T07:00:00Z", periods=3):
    return pd.date_range(start, periods=periods, freq="1h", tz="UTC")


def test_writes_one_row_per_valid_time_with_expected_columns(tmp_path):
    archive.write_day(tmp_path, "wave-a", ISSUED, _times(), _forcing(), source="meteofrance_arpege_europe")

    path = tmp_path / "2026-07-30.parquet"
    assert path.exists()
    df = pq.read_table(path).to_pandas()
    assert set(df.columns) == {"station_id", "issued", "valid_time", "lead_h", "wind_u10", "wind_v10", "source"}
    assert len(df) == 3
    row = df.iloc[0]
    assert row["station_id"] == "wave-a"
    assert row["source"] == "meteofrance_arpege_europe"
    assert row["lead_h"] == 1


def test_replaying_the_same_station_and_day_does_not_duplicate_rows(tmp_path):
    archive.write_day(tmp_path, "wave-a", ISSUED, _times(), _forcing(), source="meteofrance_arpege_europe")
    archive.write_day(tmp_path, "wave-a", ISSUED, _times(), _forcing(), source="meteofrance_arpege_europe")

    df = pq.read_table(tmp_path / "2026-07-30.parquet").to_pandas()
    assert len(df) == 3


def test_a_second_station_the_same_day_is_appended_not_overwritten(tmp_path):
    archive.write_day(tmp_path, "wave-a", ISSUED, _times(), _forcing(), source="meteofrance_arpege_europe")
    archive.write_day(tmp_path, "tide-b", ISSUED, _times(), _forcing(), source="meteofrance_arpege_europe")

    df = pq.read_table(tmp_path / "2026-07-30.parquet").to_pandas()
    assert set(df["station_id"]) == {"wave-a", "tide-b"}
    assert len(df) == 6


def test_a_neighbouring_day_is_untouched(tmp_path):
    archive.write_day(tmp_path, "wave-a", ISSUED, _times(), _forcing(), source="meteofrance_arpege_europe")
    other_issued = pd.Timestamp("2026-07-31T06:00:00Z")
    archive.write_day(tmp_path, "wave-a", other_issued, _times("2026-07-31T07:00:00Z"), _forcing("2026-07-31T06:00:00Z"), source="meteofrance_arpege_europe")

    assert (tmp_path / "2026-07-30.parquet").exists()
    assert (tmp_path / "2026-07-31.parquet").exists()
    df = pq.read_table(tmp_path / "2026-07-30.parquet").to_pandas()
    assert len(df) == 3


def test_empty_times_writes_no_rows_and_no_file(tmp_path):
    archive.write_day(tmp_path, "wave-a", ISSUED, pd.DatetimeIndex([], tz="UTC"), _forcing(), source="meteofrance_arpege_europe")

    assert not (tmp_path / "2026-07-30.parquet").exists()
