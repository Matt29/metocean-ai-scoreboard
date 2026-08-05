"""`uv run scoreboard daily [--date YYYY-MM-DD] [--dry-run]`,
`uv run scoreboard backfill --since YYYY-MM-DD [--dry-run]` and
`uv run scoreboard archive-obs [--dry-run]` — argparse facade only."""

from __future__ import annotations

import argparse
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path

from scoreboard import archive, archive_obs, backfill, daily, og
from scoreboard.config import load_env
from scoreboard.sources import SourceError, mfbuoy

DATA_DIR = Path(__file__).resolve().parents[3] / "data"


def _resolve_out_dir(dry_run: bool) -> Path:
    if dry_run:
        return Path(tempfile.mkdtemp(prefix="scoreboard-dryrun-"))
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return DATA_DIR


def _resolve_archive_dir(dry_run: bool, out_dir: Path, default: Path) -> Path:
    # Same logic as `_resolve_out_dir`: a dry-run must never write into a
    # committed archive directory — it gets its own throwaway tmp dir.
    return out_dir / default.name if dry_run else default


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

    obs_parser = sub.add_parser(
        "archive-obs", help="archive the Météo-France buoy observations before they age out"
    )
    obs_parser.add_argument(
        "--dry-run", action="store_true", help="write to a temp dir instead of data_obs_archive/"
    )

    og_parser = sub.add_parser(
        "og-images", help="write a 1200x630 Open Graph PNG per station from scores.json"
    )
    og_parser.add_argument(
        "--dry-run", action="store_true", help="write to a temp dir instead of data/"
    )

    args = parser.parse_args(argv)
    load_env()
    out_dir = _resolve_out_dir(args.dry_run)

    if args.command == "archive-obs":
        obs_dir = _resolve_archive_dir(args.dry_run, out_dir, archive.DEFAULT_OBS_ARCHIVE_DIR)
        try:
            obs, written = archive_obs.run(obs_dir, out_dir)
        except SourceError as exc:
            # Une panne Météo-France ne doit pas coûter au scoreboard son commit
            # quotidien — mais elle doit rester VISIBLE : `::warning::` remonte
            # en annotation Actions, là où un `continue-on-error` dans le YAML
            # rendrait le run vert et muet. La fenêtre de 90 h couvre ~3,5 runs,
            # donc le prochain rattrape ; c'est un avertissement, pas une
            # urgence. Un vrai plantage, lui, passe et met le job au rouge.
            print(f"::warning::archive-obs: {exc.msg}")
            return 0
        print(f"archive-obs -> {obs_dir}" + (" (dry-run)" if args.dry_run else ""))
        # Non-null par bouée et par variable : la seule preuve que la donnée est
        # là. Sortie de cron, donc lue quand quelque chose cloche.
        print(mfbuoy.non_null_counts(obs).to_string())
        print(f"  {len(written)} fichier(s) jour: {', '.join(p.name for p in written)}")
        return 0

    if args.command == "og-images":
        written = og.run(DATA_DIR / "scores.json", out_dir)
        print(f"og-images -> {out_dir}" + (" (dry-run)" if args.dry_run else ""))
        for path in written:
            print(f"  {path}")
        return 0

    if args.command == "daily":
        run_date = date.fromisoformat(args.date) if args.date else datetime.now(timezone.utc).date()
        archive_dir = _resolve_archive_dir(args.dry_run, out_dir, archive.DEFAULT_ARCHIVE_DIR)
        try:
            summary = daily.run(run_date, out_dir, archive_dir=archive_dir)
        except daily.DailyRunError as exc:
            summary = exc.summary
            status = 1
        except daily.GateConfigurationError as exc:
            print(f"::error::{exc}")
            return 2
        else:
            status = 0
        print(f"run {run_date} -> {out_dir}" + (" (dry-run)" if args.dry_run else ""))
        for station_id, result in summary.items():
            suffix = f" ({result['reason']})" if result.get("reason") else ""
            print(f"  {station_id}: {result['status']}{suffix}")
        if status:
            print("::error::no gate-passing station was published")
        return status

    since = date.fromisoformat(args.since)
    try:
        summary = backfill.run(since, out_dir)
    except daily.GateConfigurationError as exc:
        print(f"::error::{exc}")
        return 2
    print(f"backfill since {since} -> {out_dir}" + (" (dry-run)" if args.dry_run else ""))
    for station_id, dates in summary.items():
        span = f" ({dates[0]}..{dates[-1]})" if dates else ""
        print(f"  {station_id}: {len(dates)} day(s) replayed{span}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
