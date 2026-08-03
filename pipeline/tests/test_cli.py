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
