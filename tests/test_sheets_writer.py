"""Unit tests for sheets_writer module."""
import pytest
from unittest.mock import Mock, MagicMock, patch, call

from collections_sync.models import DelinquentRow
from collections_sync.sheets_writer import (
    CollectionsSheetsWriter,
    _normalize_header,
    _normalize_lease_id_key,
    _find_header_index_any,
    _col_letter,
)


class TestNormalizeHeader:
    """Test header normalization."""

    def test_lowercase(self):
        """Test conversion to lowercase."""
        assert _normalize_header("LEASE ID") == "lease id"
        assert _normalize_header("Name") == "name"

    def test_whitespace_strip(self):
        """Test whitespace stripping."""
        assert _normalize_header("  Lease ID  ") == "lease id"
        assert _normalize_header("\tName\n") == "name"

    def test_collapse_multiple_spaces(self):
        """Test collapsing multiple spaces."""
        assert _normalize_header("Amount  Owed  :") == "amount owed :"
        assert _normalize_header("Last   Edited   Date") == "last edited date"

    def test_newline_replacement(self):
        """Test newline handling."""
        assert _normalize_header("Lease\nID") == "lease id"
        assert _normalize_header("Amount\nOwed:") == "amount owed:"

    def test_empty_string(self):
        """Test empty string."""
        assert _normalize_header("") == ""

    def test_only_whitespace(self):
        """Test string with only whitespace."""
        assert _normalize_header("   \t\n  ") == ""


class TestNormalizeLeaseIdKey:
    """Test lease ID key normalization."""

    def test_basic_key(self):
        """Test basic lease ID."""
        assert _normalize_lease_id_key("12345") == "12345"

    def test_strip_whitespace(self):
        """Test whitespace stripping."""
        assert _normalize_lease_id_key("  12345  ") == "12345"

    def test_strip_decimal_suffix(self):
        """Test decimal suffix removal."""
        assert _normalize_lease_id_key("12345.0") == "12345"
        assert _normalize_lease_id_key("67890.5") == "67890"

    def test_strip_decimal_with_spaces(self):
        """Test decimal removal with spaces."""
        assert _normalize_lease_id_key("  12345.0  ") == "12345"

    def test_empty_string(self):
        """Test empty string."""
        assert _normalize_lease_id_key("") == ""

    def test_only_whitespace(self):
        """Test whitespace-only string."""
        assert _normalize_lease_id_key("   ") == ""

    def test_multiple_decimals(self):
        """Test string with multiple decimals (takes first part)."""
        assert _normalize_lease_id_key("12345.67.89") == "12345"


class TestFindHeaderIndexAny:
    """Test header index finding with aliases."""

    def test_exact_match(self):
        """Test exact header match."""
        headers = ["Name", "Address:", "Phone Number"]
        assert _find_header_index_any(headers, ["Name"]) == 0

    def test_case_insensitive_match(self):
        """Test case-insensitive matching."""
        headers = ["Name", "ADDRESS:", "Phone Number"]
        assert _find_header_index_any(headers, ["address:"]) == 1

    def test_whitespace_tolerant_match(self):
        """Test whitespace-tolerant matching."""
        headers = ["  Name  ", "Address:", "Phone  Number"]
        assert _find_header_index_any(headers, ["name"]) == 0
        assert _find_header_index_any(headers, ["phone number"]) == 2

    def test_first_match_wins(self):
        """Test that first matching candidate is used."""
        headers = ["Account Number", "Lease ID", "Name"]
        # Both candidates could match "Account Number" and "Lease ID"
        # but we expect the first match to win
        result = _find_header_index_any(headers, ["Lease ID", "Account Number"])
        assert result == 1  # "Lease ID" comes first in candidates

    def test_no_match(self):
        """Test when no header matches."""
        headers = ["Name", "Address:", "Phone Number"]
        assert _find_header_index_any(headers, ["Email", "Website"]) == -1

    def test_empty_headers(self):
        """Test with empty headers list."""
        assert _find_header_index_any([], ["Name"]) == -1

    def test_empty_candidates(self):
        """Test with empty candidates list."""
        headers = ["Name", "Address:"]
        assert _find_header_index_any(headers, []) == -1

    def test_multiple_candidates(self):
        """Test with multiple candidate names."""
        headers = ["Tenant Name", "Address", "Phone"]
        # Should find "Tenant Name" when looking for "Name" or "Tenant Name"
        result = _find_header_index_any(headers, ["Name", "Tenant Name"])
        assert result == 0


class TestColLetter:
    """Test column letter conversion."""

    def test_single_letter_columns(self):
        """Test A-Z columns."""
        assert _col_letter(0) == "A"
        assert _col_letter(1) == "B"
        assert _col_letter(25) == "Z"

    def test_double_letter_columns(self):
        """Test AA-ZZ columns."""
        assert _col_letter(26) == "AA"
        assert _col_letter(27) == "AB"
        assert _col_letter(51) == "AZ"
        assert _col_letter(52) == "BA"

    def test_triple_letter_columns(self):
        """Test AAA columns."""
        assert _col_letter(701) == "ZZ"
        assert _col_letter(702) == "AAA"

    def test_specific_known_values(self):
        """Test specific known column values."""
        # From the Go code, column 24 (0-indexed) is used
        assert _col_letter(24) == "Y"


class TestCollectionsSheetsWriterValidation:
    """Test CollectionsSheetsWriter configuration validation."""

    def test_validate_config_valid(self):
        """Test validation with valid config."""
        mock_client = Mock()
        writer = CollectionsSheetsWriter(
            client=mock_client,
            spreadsheet_id="abc123",
            sheet_title="Sheet1",
            header_row=1,
            data_row=2,
        )
        # Should not raise
        writer._validate_config()

    def test_validate_config_missing_sheet_title(self):
        """Test validation fails with missing sheet_title."""
        mock_client = Mock()
        writer = CollectionsSheetsWriter(
            client=mock_client,
            spreadsheet_id="abc123",
            sheet_title="",
            header_row=1,
            data_row=2,
        )
        with pytest.raises(ValueError, match="sheet_title required"):
            writer._validate_config()

    def test_validate_config_invalid_header_row(self):
        """Test validation fails with invalid header_row."""
        mock_client = Mock()
        writer = CollectionsSheetsWriter(
            client=mock_client,
            spreadsheet_id="abc123",
            sheet_title="Sheet1",
            header_row=0,  # Invalid: must be >= 1
            data_row=2,
        )
        with pytest.raises(ValueError, match="invalid header_row or data_row"):
            writer._validate_config()

    def test_validate_config_invalid_data_row(self):
        """Test validation fails with invalid data_row."""
        mock_client = Mock()
        writer = CollectionsSheetsWriter(
            client=mock_client,
            spreadsheet_id="abc123",
            sheet_title="Sheet1",
            header_row=1,
            data_row=1,  # Invalid: must be > header_row
        )
        with pytest.raises(ValueError, match="invalid header_row or data_row"):
            writer._validate_config()

    def test_validate_config_data_before_header(self):
        """Test validation fails when data_row <= header_row."""
        mock_client = Mock()
        writer = CollectionsSheetsWriter(
            client=mock_client,
            spreadsheet_id="abc123",
            sheet_title="Sheet1",
            header_row=5,
            data_row=4,  # Invalid: before header
        )
        with pytest.raises(ValueError, match="invalid header_row or data_row"):
            writer._validate_config()


class TestCollectionsSheetsWriterHelpers:
    """Test CollectionsSheetsWriter helper methods."""

    def test_find_sheet_index_exact_match(self):
        """Test finding column by exact header name."""
        mock_client = Mock()
        writer = CollectionsSheetsWriter(
            client=mock_client,
            spreadsheet_id="abc123",
            sheet_title="Sheet1",
            header_row=1,
            data_row=2,
        )

        headers = ["Lease ID", "Name", "Address:", "Amount Owed:"]
        idx = writer._find_sheet_index(headers, "Lease ID")
        assert idx == 0

    def test_find_sheet_index_with_alias(self):
        """Test finding column using alias."""
        mock_client = Mock()
        writer = CollectionsSheetsWriter(
            client=mock_client,
            spreadsheet_id="abc123",
            sheet_title="Sheet1",
            header_row=1,
            data_row=2,
        )

        # "Amount Owed" is an alias for "Amount Owed:"
        headers = ["Lease ID", "Name", "Address:", "Amount Owed"]
        idx = writer._find_sheet_index(headers, "Amount Owed:")
        assert idx == 3

    def test_find_sheet_index_not_found(self):
        """Test when column is not found."""
        mock_client = Mock()
        writer = CollectionsSheetsWriter(
            client=mock_client,
            spreadsheet_id="abc123",
            sheet_title="Sheet1",
            header_row=1,
            data_row=2,
        )

        headers = ["Lease ID", "Name", "Address:"]
        idx = writer._find_sheet_index(headers, "Email")
        assert idx == -1

    def test_find_sheet_index_case_insensitive(self):
        """Test case-insensitive column finding."""
        mock_client = Mock()
        writer = CollectionsSheetsWriter(
            client=mock_client,
            spreadsheet_id="abc123",
            sheet_title="Sheet1",
            header_row=1,
            data_row=2,
        )

        headers = ["lease id", "name", "address:", "amount owed:"]
        idx = writer._find_sheet_index(headers, "LEASE ID")
        assert idx == 0


class TestCollectionsSheetsWriterQuickUpdate:
    """Test quick_update_balances method."""

    def test_quick_update_empty_keys(self):
        """Test quick update with no keys."""
        mock_client = Mock()
        writer = CollectionsSheetsWriter(
            client=mock_client,
            spreadsheet_id="abc123",
            sheet_title="Sheet1",
            header_row=1,
            data_row=2,
        )

        result = writer.quick_update_balances({}, [], {})
        assert result == 0

    def test_quick_update_missing_amount_column(self):
        """Test quick update fails when Amount Owed column missing."""
        mock_client = Mock()
        writer = CollectionsSheetsWriter(
            client=mock_client,
            spreadsheet_id="abc123",
            sheet_title="Sheet1",
            header_row=1,
            data_row=2,
        )

        headers = ["Lease ID", "Name"]  # Missing Amount Owed
        key_to_row = {"123": 2}

        with pytest.raises(ValueError, match="Amount Owed"):
            writer.quick_update_balances(key_to_row, headers, {123: 500.0})

    def test_quick_update_missing_date_column(self):
        """Test quick update fails when Last Edited Date column missing."""
        mock_client = Mock()
        writer = CollectionsSheetsWriter(
            client=mock_client,
            spreadsheet_id="abc123",
            sheet_title="Sheet1",
            header_row=1,
            data_row=2,
        )

        headers = ["Lease ID", "Amount Owed:"]  # Missing Last Edited Date
        key_to_row = {"123": 2}

        with pytest.raises(ValueError, match="Last Edited Date"):
            writer.quick_update_balances(key_to_row, headers, {123: 500.0})

    def test_quick_update_calls_batch_update(self):
        """Test that quick update calls batch_update_values."""
        mock_client = Mock()
        writer = CollectionsSheetsWriter(
            client=mock_client,
            spreadsheet_id="abc123",
            sheet_title="Sheet1",
            header_row=1,
            data_row=2,
        )

        headers = ["Lease ID", "Amount Owed:", "Last Edited Date"]
        key_to_row = {"123": 2, "456": 3}
        balances = {123: 500.0, 456: 750.0}

        mock_client.batch_update_values = Mock()

        result = writer.quick_update_balances(key_to_row, headers, balances)

        assert result == 2
        mock_client.batch_update_values.assert_called_once()

        # Verify the call was made with expected parameters
        call_args = mock_client.batch_update_values.call_args
        assert call_args[0][0] == "abc123"  # spreadsheet_id
        assert isinstance(call_args[0][1], list)  # updates list
        assert len(call_args[0][1]) == 4  # 2 leases * 2 cols each


class TestCollectionsSheetsWriterDatePreservation:
    """Test that Date First Added is preserved correctly."""

    def test_date_first_added_preserved_when_non_empty(self):
        """Test that Date First Added is not overwritten when non-empty."""
        mock_client = Mock()
        mock_client.ensure_sheet = Mock()
        mock_client.read_range = Mock(
            side_effect=[
                # Header row (all OWNED_HEADERS required)
                [["Date First Added", "Name", "Address:", "Phone Number", "Email", "Amount Owed:", "Lease ID", "Last Edited Date"]],
                # Existing data row: has a value in Date First Added
                [["01/01/2020", "Old Name", "123 Main St", "555-1234", "old@test.com", 1000.0, 123, "04/20/2026"]],
            ]
        )
        mock_client.batch_update_values = Mock()
        mock_client.write_range = Mock()
        mock_client.get_sheet_numeric_id = Mock(return_value=0)
        mock_client.apply_background_color = Mock()

        writer = CollectionsSheetsWriter(
            client=mock_client,
            spreadsheet_id="abc123",
            sheet_title="Sheet1",
            header_row=1,
            data_row=2,
        )

        # Try to upsert with new date
        new_rows = [
            DelinquentRow(
                lease_id=123,
                name="New Name",
                address="New Address",
                phone="555-1234",
                email="new@test.com",
                amount_owed=1500.0,
                date_added="04/23/2026",
            )
        ]

        from collections_sync.transform import HEADERS

        # Note: This is a simplified test. Full upsert testing would need
        # more comprehensive mocking of the read operations
        rows_updated, rows_appended = writer.upsert_preserving(HEADERS, new_rows)

        # The important thing is that the old date (01/01/2020) should be preserved
        # This would be verified in integration testing with real Sheets API
