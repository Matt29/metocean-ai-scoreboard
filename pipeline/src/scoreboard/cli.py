"""`uv run scoreboard daily [--date YYYY-MM-DD] [--dry-run]` — argparse facade only."""

from __future__ import annotations

import argparse
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path

from scoreboard import daily

DATA_DIR = Path(__file__).resolve().parents[3] / "data"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="scoreboard")
    sub = parser.add_subparsers(dest="command", required=True)

    daily_parser = sub.add_parser("daily", help="predict, score, publish for one day")
    daily_parser.add_argument("--date", help="run date YYYY-MM-DD (default: today UTC)")
    daily_parser.add_argument(
        "--dry-run", action="store_true", help="write to a temp dir instead of data/"
    )

    args = parser.parse_args(argv)
    run_date = date.fromisoformat(args.date) if args.date else datetime.now(timezone.utc).date()

    if args.dry_run:
        out_dir = Path(tempfile.mkdtemp(prefix="scoreboard-dryrun-"))
    else:
        out_dir = DATA_DIR
        out_dir.mkdir(parents=True, exist_ok=True)

    summary = daily.run(run_date, out_dir)

    print(f"run {run_date} -> {out_dir}" + (" (dry-run)" if args.dry_run else ""))
    for station_id, result in summary.items():
        suffix = f" ({result['reason']})" if result.get("reason") else ""
        print(f"  {station_id}: {result['status']}{suffix}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
