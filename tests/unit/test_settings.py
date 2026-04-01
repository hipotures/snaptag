from snapgit.common.settings import Settings


def test_default_settings_are_local_and_sqlite():
    settings = Settings()
    assert settings.app_env == "dev"
    assert settings.database_url.startswith("sqlite:///")
    assert settings.worker_concurrency == 1
