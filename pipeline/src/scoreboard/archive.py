"""Two archives, one rule: keep what the daily run already had in hand, because
GitHub Actions is stateless — anything not committed does not exist tomorrow.
Both write one Parquet per day into a directory that IS committed (unlike
`data_train/`), through the same atomic `_write_atomic`.

They differ in their idempotency model, and that difference is load-bearing:

- `write_day` — the served forecast, keyed by (day file, station_id): replaying
  a station's day *replaces* its rows, because a fresh issue legitimately drops
  leads the previous one covered.
- `write_obs_days` — the Météo-France buoy observations, merged on an
  observation key: consecutive runs overlap by design, so replacing would
  delete hours only the earlier run ever saw. See its own docstring.

The rest of this header describes `write_day`.

Archive the wind forecast actually served for inference, one Parquet file per
issuance day, in `pipeline/data_forecast_archive/`.

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

# Same rationale one level further upstream: `write_obs_days` keeps the
# Météo-France buoy observations, which their API only serves on a rolling
# window and never archives (see `sources/mfbuoy.py`). Committed for the same
# reason as above — Actions is stateless.
DEFAULT_OBS_ARCHIVE_DIR = Path(__file__).resolve().parents[2] / "data_obs_archive"


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

    _write_atomic(path, rows)


def write_obs_days(archive_dir: Path, obs: pd.DataFrame, *, key: list[str]) -> list[Path]:
    """Split `obs` by observation day and merge each day into its own parquet.

    Observations, unlike a forecast, arrive as one rolling window that straddles
    several calendar days — hence the split here rather than a caller-supplied
    day. Idempotence is a merge on `key` (keep the newest), NOT a
    replace-the-station's-rows like `write_day`: today's file is completed by
    tomorrow's overlapping window, so wiping a buoy's rows before rewriting
    would drop the hours only the earlier run had seen.

    Returns the day files written, for the caller to log.
    """
    if obs.empty:
        return []

    days = obs["validity_time"].str.slice(0, 10)
    written = []
    for day, rows in obs.groupby(days, sort=True):
        path = archive_dir / f"{day}.parquet"
        if path.exists():
            rows = pd.concat([pd.read_parquet(path), rows], ignore_index=True)
        rows = rows.drop_duplicates(key, keep="last").sort_values(key)
        _write_atomic(path, rows.reset_index(drop=True))
        written.append(path)
    return written


def _write_atomic(path: Path, rows: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # mkstemp only to reserve a unique name (parquet needs its own file handle,
    # unlike json.dumps' os.fdopen in publish.py) — close the fd immediately.
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    os.close(fd)
    try:
        rows.to_parquet(tmp_name, index=False)
        os.replace(tmp_name, path)  # atomic within the same directory/filesystem
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise
