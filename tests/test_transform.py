"""Unit tests for transform module (column definitions and row transformation)."""
import pytest

from collections_sync.models import DelinquentRow
from collections_sync.transform import HEADERS, OWNED_HEADERS, KEY_HEADER, to_sheet_values


class TestHeaders:
    """Test header definitions."""

    def test_headers_count(self):
        """Verify exact number of columns."""
        assert len(HEADERS) == 27


    def test_key_header_in_headers(self):
        """Verify KEY_HEADER exists in HEADERS."""
        assert KEY_HEADER in HEADERS

    def test_owned_headers_subset(self):
        """Verify OWNED_HEADERS are subset of HEADERS."""
        assert OWNED_HEADERS.issubset(set(HEADERS))

    def test_owned_headers_count(self):
        """Verify expected owned columns count."""
        assert len(OWNED_HEADERS) == 8

    def test_owned_headers_have_required_columns(self):
        """Verify all required owned columns are present."""
        required = {
            "Date First Added",
            "Name",
            "Address:",
            "Phone Number",
            "Email",
            "Amount Owed:",
            "Lease ID",
            "Last Edited Date",
        }
        assert OWNED_HEADERS == required

    def test_key_header_is_lease_id(self):
        """Verify KEY_HEADER is Lease ID."""
        assert KEY_HEADER == "Lease ID"

    def test_headers_order(self):
        """Verify headers are in expected order."""
        assert HEADERS[0] == "Date First Added"
        assert HEADERS[1] == "Name"
        assert HEADERS[2] == "Address:"
        assert HEADERS[24] == "Lease ID"


class TestToSheetValues:
    """Test DelinquentRow to sheet values conversion."""

    def test_empty_rows(self):
        """Test with empty row list."""
        result = to_sheet_values([])
        assert result == []

    def test_single_row(self):
        """Test with single row."""
        row = DelinquentRow(
            lease_id=12345,
            name="John Doe",
            address="123 Main St",
            phone="555-1234",
            email="john@example.com",
            amount_owed=1500.50,
            date_added="04/23/2026",
        )

        result = to_sheet_values([row])
        assert len(result) == 1
        assert len(result[0]) == 27

    def test_row_width_matches_headers(self):
        """Test that all output rows match HEADERS width."""
        rows = [
            DelinquentRow(
                lease_id=1,
                name="A",
                address="B",
                phone="C",
                email="D",
                amount_owed=100.0,
                date_added="01/01/2026",
            ),
            DelinquentRow(
                lease_id=2,
                name="E",
                address="F",
                phone="G",
                email="H",
                amount_owed=200.0,
                date_added="01/02/2026",
            ),
        ]

        result = to_sheet_values(rows)
        for row in result:
            assert len(row) == len(HEADERS)

    def test_column_placement_lease_id(self):
        """Test that Lease ID is placed in correct column."""
        row = DelinquentRow(
            lease_id=99999,
            name="Test",
            address="Addr",
            phone="555",
            email="test@test.com",
            amount_owed=50.0,
            date_added="04/23/2026",
        )

        result = to_sheet_values([row])
        lease_id_idx = HEADERS.index("Lease ID")
        assert result[0][lease_id_idx] == 99999

    def test_column_placement_name(self):
        """Test that Name is placed in correct column."""
        row = DelinquentRow(
            lease_id=1,
            name="John Doe",
            address="Addr",
            phone="555",
            email="test@test.com",
            amount_owed=50.0,
            date_added="04/23/2026",
        )

        result = to_sheet_values([row])
        name_idx = HEADERS.index("Name")
        assert result[0][name_idx] == "John Doe"

    def test_column_placement_amount_owed(self):
        """Test that Amount Owed is placed in correct column."""
        row = DelinquentRow(
            lease_id=1,
            name="Test",
            address="Addr",
            phone="555",
            email="test@test.com",
            amount_owed=1234.56,
            date_added="04/23/2026",
        )

        result = to_sheet_values([row])
        amount_idx = HEADERS.index("Amount Owed:")
        assert result[0][amount_idx] == 1234.56

    def test_column_placement_date_added(self):
        """Test that Date Added is placed in correct column."""
        row = DelinquentRow(
            lease_id=1,
            name="Test",
            address="Addr",
            phone="555",
            email="test@test.com",
            amount_owed=50.0,
            date_added="04/23/2026",
        )

        result = to_sheet_values([row])
        date_idx = HEADERS.index("Date First Added")
        assert result[0][date_idx] == "04/23/2026"

    def test_non_owned_columns_empty(self):
        """Test that non-owned columns are empty strings."""
        row = DelinquentRow(
            lease_id=1,
            name="Test",
            address="Addr",
            phone="555",
            email="test@test.com",
            amount_owed=50.0,
            date_added="04/23/2026",
        )

        result = to_sheet_values([row])
        owned_indices = {HEADERS.index(h) for h in OWNED_HEADERS}

        for i, val in enumerate(result[0]):
            if i not in owned_indices:
                assert val == "", f"Column {i} ({HEADERS[i]}) should be empty, got {val!r}"

    def test_last_edited_date_set(self):
        """Test that Last Edited Date is set to today."""
        import datetime

        row = DelinquentRow(
            lease_id=1,
            name="Test",
            address="Addr",
            phone="555",
            email="test@test.com",
            amount_owed=50.0,
            date_added="04/23/2026",
        )

        result = to_sheet_values([row])
        date_idx = HEADERS.index("Last Edited Date")
        today = datetime.datetime.now().strftime("%m/%d/%Y")
        assert result[0][date_idx] == today

    def test_multiple_rows_different_values(self):
        """Test that multiple rows maintain separate values."""
        rows = [
            DelinquentRow(
                lease_id=111,
                name="Alice",
                address="111 A St",
                phone="111-1111",
                email="alice@test.com",
                amount_owed=100.0,
                date_added="01/01/2026",
            ),
            DelinquentRow(
                lease_id=222,
                name="Bob",
                address="222 B St",
                phone="222-2222",
                email="bob@test.com",
                amount_owed=200.0,
                date_added="02/02/2026",
            ),
        ]

        result = to_sheet_values(rows)
        assert len(result) == 2

        # Check first row
        lease_idx = HEADERS.index("Lease ID")
        name_idx = HEADERS.index("Name")
        assert result[0][lease_idx] == 111
        assert result[0][name_idx] == "Alice"

        # Check second row
        assert result[1][lease_idx] == 222
        assert result[1][name_idx] == "Bob"

    def test_special_characters_in_name(self):
        """Test that special characters are preserved."""
        row = DelinquentRow(
            lease_id=1,
            name="O'Brien, Jr.",
            address="123 St. Mary's Ave",
            phone="555-1234",
            email="test@test.com",
            amount_owed=50.0,
            date_added="04/23/2026",
        )

        result = to_sheet_values([row])
        name_idx = HEADERS.index("Name")
        assert result[0][name_idx] == "O'Brien, Jr."

    def test_zero_amount_owed(self):
        """Test that zero amount is preserved."""
        row = DelinquentRow(
            lease_id=1,
            name="Test",
            address="Addr",
            phone="555",
            email="test@test.com",
            amount_owed=0.0,
            date_added="04/23/2026",
        )

        result = to_sheet_values([row])
        amount_idx = HEADERS.index("Amount Owed:")
        assert result[0][amount_idx] == 0.0

    def test_large_amount_owed(self):
        """Test that large amounts are preserved."""
        row = DelinquentRow(
            lease_id=1,
            name="Test",
            address="Addr",
            phone="555",
            email="test@test.com",
            amount_owed=99999.99,
            date_added="04/23/2026",
        )

        result = to_sheet_values([row])
        amount_idx = HEADERS.index("Amount Owed:")
        assert result[0][amount_idx] == 99999.99

    def test_empty_optional_fields(self):
        """Test row with empty optional fields."""
        row = DelinquentRow(
            lease_id=1,
            name="",
            address="",
            phone="",
            email="",
            amount_owed=0.0,
            date_added="04/23/2026",
        )

        result = to_sheet_values([row])
        assert len(result) == 1
        assert len(result[0]) == 27
