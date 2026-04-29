"""Unit tests for configuration aliases."""

from collections_sync.config import CollectionsSyncConfig


CONFIG_ENV_KEYS = [
    "BUILDIUM_KEY",
    "BUILDIUM_SECRET",
    "BUILDIUM_CLIENT_ID",
    "BUILDIUM_CLIENT_SECRET",
    "SHEET_ID",
    "SPREADSHEET_ID",
    "WORKSHEET_NAME",
    "SHEET_TITLE",
    "BUILDIUM_API_URL",
    "BUILDIUM_BASE_URL",
    "TEST_SHEET_ID",
]


def clear_config_env(monkeypatch):
    for key in CONFIG_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


def test_documented_env_aliases_load(monkeypatch):
    """README/.env.example variable names should be accepted."""
    clear_config_env(monkeypatch)
    monkeypatch.setenv("BUILDIUM_KEY", "client-id")
    monkeypatch.setenv("BUILDIUM_SECRET", "client-secret")
    monkeypatch.setenv("SPREADSHEET_ID", "spreadsheet")
    monkeypatch.setenv("SHEET_TITLE", "Collections")
    monkeypatch.setenv("BUILDIUM_API_URL", "https://api.buildium.com/v1")

    cfg = CollectionsSyncConfig(_env_file=None)

    assert cfg.buildium_key == "client-id"
    assert cfg.buildium_secret == "client-secret"
    assert cfg.sheet_id == "spreadsheet"
    assert cfg.worksheet_name == "Collections"
    assert cfg.buildium_base_url == "https://api.buildium.com/v1"


def test_client_env_names_still_load(monkeypatch):
    """Deployment variable names should remain supported."""
    clear_config_env(monkeypatch)
    monkeypatch.setenv("BUILDIUM_CLIENT_ID", "client-id")
    monkeypatch.setenv("BUILDIUM_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv("SHEET_ID", "spreadsheet")
    monkeypatch.setenv("WORKSHEET_NAME", "Collections")
    monkeypatch.setenv("BUILDIUM_BASE_URL", "https://api.example.test/v1")

    cfg = CollectionsSyncConfig(_env_file=None)

    assert cfg.buildium_key == "client-id"
    assert cfg.buildium_secret == "client-secret"
    assert cfg.effective_sheet_id == "spreadsheet"
    assert cfg.worksheet_name == "Collections"
    assert cfg.buildium_base_url == "https://api.example.test/v1"


def test_buildium_base_url_defaults_to_v1(monkeypatch):
    clear_config_env(monkeypatch)
    cfg = CollectionsSyncConfig(_env_file=None)

    assert cfg.buildium_base_url == "https://api.buildium.com/v1"
