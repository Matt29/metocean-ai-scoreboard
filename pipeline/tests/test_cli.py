from datetime import date, datetime, timedelta, timezone

from scoreboard import cli, daily
from scoreboard.config import load_env


def test_load_env_parses_and_never_overrides(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text(
        "# commentaire\n"
        "\n"
        'CANDHIS_API_KEY="secret"\n'
        "DEJA_LA=fichier\n"
        "ligne sans egal\n"
    )
    monkeypatch.delenv("CANDHIS_API_KEY", raising=False)
    monkeypatch.setenv("DEJA_LA", "environnement")

    load_env(env)

    import os
    assert os.environ["CANDHIS_API_KEY"] == "secret"
    assert os.environ["DEJA_LA"] == "environnement"  # l'env réel gagne


def test_load_env_missing_file_is_noop(tmp_path):
    load_env(tmp_path / "absent.env")  # ne doit pas lever


def test_daily_command_returns_nonzero_when_no_gate_passing_station_is_published(monkeypatch, capsys):
    failure = daily.DailyRunError(
        date(2026, 7, 30), {"wave-a": {"status": "missing", "reason": "candhis 429"}}
    )
    monkeypatch.setattr(cli.daily, "run", lambda *args, **kwargs: (_ for _ in ()).throw(failure))

    assert cli.main(["daily", "--date", "2026-07-30", "--dry-run"]) == 1
    output = capsys.readouterr().out
    assert "wave-a: missing (candhis 429)" in output


def test_daily_command_returns_nonzero_for_an_invalid_gate(monkeypatch, capsys):
    monkeypatch.setattr(
        cli.daily,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(daily.GateConfigurationError("gate is empty")),
    )

    assert cli.main(["daily", "--date", "2026-07-30", "--dry-run"]) == 2
    assert "::error::gate is empty" in capsys.readouterr().out


def test_backfill_command_returns_nonzero_for_an_invalid_gate(monkeypatch, capsys):
    monkeypatch.setattr(
        cli.backfill,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            daily.GateConfigurationError("gate is incomplete")
        ),
    )

    assert cli.main(["backfill", "--since", "2026-07-01", "--dry-run"]) == 2
    assert "::error::gate is incomplete" in capsys.readouterr().out


def test_backfill_warns_when_since_covers_no_replayable_day(monkeypatch, capsys):
    """Un `--since` au jour courant ne rejoue rien : le job reste vert, mais il
    doit le DIRE — sinon le trou qu'on croyait comblé reste ouvert en silence."""
    monkeypatch.setattr(cli.backfill, "run", lambda *args, **kwargs: {})
    today = datetime.now(timezone.utc).date()

    assert cli.main(["backfill", "--since", today.isoformat(), "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "::warning::backfill:" in out
    assert "aucun jour rejouable" in out


def test_backfill_stays_quiet_when_since_is_replayable(monkeypatch, capsys):
    monkeypatch.setattr(cli.backfill, "run", lambda *args, **kwargs: {})
    yesterday = datetime.now(timezone.utc).date() - timedelta(days=1)

    assert cli.main(["backfill", "--since", yesterday.isoformat(), "--dry-run"]) == 0
    assert "::warning::" not in capsys.readouterr().out
