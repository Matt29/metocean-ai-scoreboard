"""Archive the wind forecast actually served for inference, one Parquet file per
issuance day, in `pipeline/data_forecast_archive/` — a directory that IS committed
(unlike `data_train/`), because GitHub Actions is stateless: anything not committed
does not exist tomorrow.

Why: the model trains on ERA5 (reanalysis, perfect hindsight) but serves ARPEGE
forecasts (see `docs/data-sources.md` §4bis) — a skew that can only be *removed*,
not reduced, by retraining on real served forecasts once enough have accumulated.
`daily.py` already fetches exactly that forecast every run and discards it; this
module is the only change: keep what was already in hand.

Columns are whatever the forcing frame actually carries, not a hardcoded list:
the wave path serves the 3-model frame (`wind.MULTI_FORCING_COLUMNS`, archived
as `source="openmeteo:multi"`), the tide path the single ARPEGE run — and both
land in the same day file, each with the other's columns as NaN. The reader
here presupposes nothing, so parquets written before Task 6 stay readable.

Idempotent by (day file, station_id): replaying the same day for the same station
replaces its rows rather than duplicating them; other stations' rows already
written for that day, and every other day's file, are untouched. Atomic write
(tmp + rename), same discipline as `publish.py` — this runs unsupervised.

Not a byte-for-byte copy of what inference was served: a degraded fetch is
covered by `features._aligned_forcing`'s coverage floor with
`_NEUTRAL_FORCING = 0.0`, but this archive stores the raw forecast reindexed
onto `valid_times` with no such fill — a gap here reads as `NaN`, not `0.0`.
That is deliberate: this corpus is the *ground truth* forecast, which is what
a future retrain needs, not a replay of the inference-time neutral fallback.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pandas as pd

DEFAULT_ARCHIVE_DIR = Path(__file__).resolve().parents[2] / "data_forecast_archive"


def write_day(
    archive_dir: Path,
    station_id: str,
    issued: pd.Timestamp,
    valid_times: pd.DatetimeIndex,
    forcing: pd.DataFrame,
    *,
    source: str,
) -> None:
    """Append/replace `station_id`'s rows in `archive_dir/<issued date>.parquet`.

    `valid_times` are the times the inference actually consumed (one row per
    lead) — a station with nothing to archive (inference failed, empty series)
    must pass an empty index rather than call this at all; an empty index here
    is a no-op, not an empty file.
    """
    if len(valid_times) == 0:
        return

    path = archive_dir / f"{issued.date().isoformat()}.parquet"
    forcing_at_valid = forcing.reindex(valid_times, method="nearest", tolerance=pd.Timedelta("1h"))
    rows = pd.DataFrame(
        {
            "station_id": station_id,
            "issued": issued.isoformat(),
            "valid_time": [t.isoformat() for t in valid_times],
            "lead_h": ((valid_times - issued) / pd.Timedelta(hours=1)).round().astype(int),
            **{col: forcing_at_valid[col].to_numpy() for col in forcing.columns},
            "source": source,
        }
    )

    if path.exists():
        existing = pd.read_parquet(path)
        existing = existing[existing["station_id"] != station_id]
        rows = pd.concat([existing, rows], ignore_index=True)

    archive_dir.mkdir(parents=True, exist_ok=True)
    # mkstemp only to reserve a unique name (parquet needs its own file handle,
    # unlike json.dumps' os.fdopen in publish.py) — close the fd immediately.
    fd, tmp_name = tempfile.mkstemp(dir=archive_dir, prefix=path.name + ".", suffix=".tmp")
    os.close(fd)
    try:
        rows.to_parquet(tmp_name, index=False)
        os.replace(tmp_name, path)  # atomic within the same directory/filesystem
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise
