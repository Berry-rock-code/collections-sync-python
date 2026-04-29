"""Configuration for collections-sync service."""
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import AliasChoices, Field


class CollectionsSyncConfig(BaseSettings):
    """Configuration loaded from environment variables and .env file."""
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    # Sheet configuration
    sheet_id: str = Field(default="", validation_alias=AliasChoices("SHEET_ID", "SPREADSHEET_ID"))
    test_sheet_id: str = Field(default="", alias="TEST_SHEET_ID")
    worksheet_name: str = Field(default="", validation_alias=AliasChoices("WORKSHEET_NAME", "SHEET_TITLE"))
    header_row: int = 1
    data_row: int = 2

    # Buildium API credentials (accept both old and new names)
    buildium_key: str = Field(
        default="",
        validation_alias=AliasChoices("BUILDIUM_CLIENT_ID", "BUILDIUM_KEY"),
    )
    buildium_secret: str = Field(
        default="",
        validation_alias=AliasChoices("BUILDIUM_CLIENT_SECRET", "BUILDIUM_SECRET"),
    )
    buildium_base_url: str = Field(
        default="https://api.buildium.com/v1",
        validation_alias=AliasChoices("BUILDIUM_BASE_URL", "BUILDIUM_API_URL"),
    )

    # Google credentials
    google_sheets_credentials_path: str = ""

    # Server
    port: int = 8080

    # Timeouts (seconds)
    bal_timeout: int = 60
    lease_timeout: int = 60
    tenant_timeout: int = 60
    tenant_sleep_ms: int = 250

    # Distributed lock (opt-in robustness feature)
    sync_lock_sheet: str = Field(default="_sync_lock", alias="SYNC_LOCK_SHEET")
    sync_lock_timeout_seconds: int = Field(default=30, alias="SYNC_LOCK_TIMEOUT_SECONDS")
    sync_lock_stale_seconds: int = Field(default=300, alias="SYNC_LOCK_STALE_SECONDS")

    # Write reliability (opt-in robustness feature)
    sync_write_chunk_size: int = Field(default=200, alias="SYNC_WRITE_CHUNK_SIZE")
    sync_verify_checksums: bool = Field(default=False, alias="SYNC_VERIFY_CHECKSUMS")
    sync_max_retries: int = Field(default=2, alias="SYNC_MAX_RETRIES")
    sync_retry_backoff_ms: int = Field(default=2000, alias="SYNC_RETRY_BACKOFF_MS")
    sync_enable_atomic: bool = Field(default=False, alias="SYNC_ENABLE_ATOMIC")

    @property
    def effective_sheet_id(self) -> str:
        """Return test_sheet_id if set, otherwise sheet_id."""
        return self.test_sheet_id or self.sheet_id

    def validate_required(self) -> None:
        """Check required fields are set.

        Raises:
            ValueError: If any required field is missing.
        """
        errors = []
        if not self.effective_sheet_id:
            errors.append("SHEET_ID (or SPREADSHEET_ID) is required")
        if not self.worksheet_name:
            errors.append("WORKSHEET_NAME (or SHEET_TITLE) is required")
        if not self.buildium_key:
            errors.append("BUILDIUM_KEY is required")
        if not self.buildium_secret:
            errors.append("BUILDIUM_SECRET is required")

        if errors:
            raise ValueError("; ".join(errors))
