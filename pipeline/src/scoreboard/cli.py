"""`uv run scoreboard daily [--date YYYY-MM-DD] [--dry-run]` and
`uv run scoreboard backfill --since YYYY-MM-DD [--dry-run]` — argparse facade only."""

from __future__ import annotations

import argparse
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path

from scoreboard import archive, backfill, daily

DATA_DIR = Path(__file__).resolve().parents[3] / "data"


def _resolve_out_dir(dry_run: bool) -> Path:
    if dry_run:
        return Path(tempfile.mkdtemp(prefix="scoreboard-dryrun-"))
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return DATA_DIR


def _resolve_archive_dir(dry_run: bool, out_dir: Path) -> Path:
    # Same logic as `_resolve_out_dir`: a dry-run must never write into the
    # committed `data_forecast_archive/` — it gets its own throwaway tmp dir.
    return out_dir / "data_forecast_archive" if dry_run else archive.DEFAULT_ARCHIVE_DIR


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="scoreboard")
    sub = parser.add_subparsers(dest="command", required=True)

    daily_parser = sub.add_parser("daily", help="predict, score, publish for one day")
    daily_parser.add_argument("--date", help="run date YYYY-MM-DD (default: today UTC)")
    daily_parser.add_argument(
        "--dry-run", action="store_true", help="write to a temp dir instead of data/"
    )

    backfill_parser = sub.add_parser(
        "backfill", help="replay every missing day up to yesterday (one deep fetch per source)"
    )
    backfill_parser.add_argument("--since", required=True, help="earliest date YYYY-MM-DD to backfill")
    backfill_parser.add_argument(
        "--dry-run", action="store_true", help="write to a temp dir instead of data/"
    )

    args = parser.parse_args(argv)
    out_dir = _resolve_out_dir(args.dry_run)

    if args.command == "daily":
        run_date = date.fromisoformat(args.date) if args.date else datetime.now(timezone.utc).date()
        archive_dir = _resolve_archive_dir(args.dry_run, out_dir)
        summary = daily.run(run_date, out_dir, archive_dir=archive_dir)
        print(f"run {run_date} -> {out_dir}" + (" (dry-run)" if args.dry_run else ""))
        for station_id, result in summary.items():
            suffix = f" ({result['reason']})" if result.get("reason") else ""
            print(f"  {station_id}: {result['status']}{suffix}")
        return 0

    since = date.fromisoformat(args.since)
    summary = backfill.run(since, out_dir)
    print(f"backfill since {since} -> {out_dir}" + (" (dry-run)" if args.dry_run else ""))
    for station_id, dates in summary.items():
        span = f" ({dates[0]}..{dates[-1]})" if dates else ""
        print(f"  {station_id}: {len(dates)} day(s) replayed{span}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
