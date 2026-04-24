"""Configuration for collections-sync service."""
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field, field_validator


class CollectionsSyncConfig(BaseSettings):
    """Configuration loaded from environment variables and .env file."""
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    # Sheet configuration
    sheet_id: str = Field(default="", alias="SHEET_ID")
    test_sheet_id: str = Field(default="", alias="TEST_SHEET_ID")
    worksheet_name: str = Field(default="", alias="WORKSHEET_NAME")
    header_row: int = 1
    data_row: int = 2

    # Buildium API credentials (accept both old and new names)
    buildium_key: str = Field(default="", alias="BUILDIUM_CLIENT_ID")
    buildium_secret: str = Field(default="", alias="BUILDIUM_CLIENT_SECRET")
    buildium_base_url: str = Field(default="https://api.buildium.com", alias="BUILDIUM_BASE_URL")

    # Google credentials
    google_sheets_credentials_path: str = ""

    # Server
    port: int = 8080

    # Timeouts (seconds)
    bal_timeout: int = 60
    lease_timeout: int = 60
    tenant_timeout: int = 60
    tenant_sleep_ms: int = 250

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
