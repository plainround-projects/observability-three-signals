import pytest

from three_signals.infrastructure.settings import BackSettings, FrontSettings, SettingsError

BACK_ENV = {"DATABASE_URL": "postgresql://reader@db/orders"}


def test_back_defaults_match_the_lesson():
    settings = BackSettings.from_env(BACK_ENV)
    assert (settings.slow_query_probability, settings.slow_query_seconds) == (0.05, 1.5)
    assert (settings.port, settings.bind_host, settings.service_name) == (8081, "127.0.0.1", "back")
    assert settings.slow_query_seed is None


def test_back_reads_overrides():
    settings = BackSettings.from_env(
        {
            **BACK_ENV,
            "SLOW_QUERY_PROBABILITY": "0.5",
            "SLOW_QUERY_SEED": "42",
            "BACK_PORT": "9000",
            "LOG_LEVEL": "debug",
        }
    )
    assert (settings.slow_query_probability, settings.slow_query_seed, settings.port) == (0.5, 42, 9000)
    assert settings.log_level == "DEBUG"


@pytest.mark.parametrize(
    "overrides",
    [
        {"DATABASE_URL": ""},
        {"SLOW_QUERY_PROBABILITY": "1.5"},
        {"SLOW_QUERY_PROBABILITY": "often"},
        {"BACK_PORT": "0"},
        {"LOG_LEVEL": "LOUD"},
    ],
)
def test_back_rejects_missing_or_out_of_range_values(overrides):
    with pytest.raises(SettingsError):
        BackSettings.from_env({**BACK_ENV, **overrides})


def test_front_requires_the_back_url_and_strips_a_trailing_slash():
    assert FrontSettings.from_env({"BACK_URL": "http://back:8081/"}).back_url == "http://back:8081"
    with pytest.raises(SettingsError):
        FrontSettings.from_env({})


def test_front_validates_the_timeout():
    with pytest.raises(SettingsError):
        FrontSettings.from_env({"BACK_URL": "http://back", "BACK_TIMEOUT_SECONDS": "0"})
