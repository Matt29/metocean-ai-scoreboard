from scoreboard.sources import make_session


def test_make_session_retries_transient_errors():
    session = make_session()
    adapter = session.get_adapter("https://example.com")
    assert adapter.max_retries.total == 3
