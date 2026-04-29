"""Integration tests for atomic operations and locking."""
import asyncio
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch, AsyncMock
import pytest

from collections_sync.data_validator import DataValidator
from collections_sync.exceptions import DataCorruptionError, DataValidationError
from collections_sync.lock_manager import SyncLockManager
from collections_sync.models import DelinquentRow


class TestSyncLockManager:
    """Tests for distributed locking via Google Sheets."""

    def test_acquire_when_cell_empty_succeeds(self):
        """Test acquiring lock when cell is empty."""
        mock_client = MagicMock()
        mock_client.ensure_sheet = MagicMock()
        mock_client.read_range = MagicMock(return_value=[[""]]) # Empty cell
        mock_client.write_range = MagicMock()

        lock_mgr = SyncLockManager(
            mock_client,
            "spreadsheet_id",
            acquire_timeout=5.0,
        )

        result = lock_mgr.acquire()

        assert result is True
        mock_client.ensure_sheet.assert_called()
        mock_client.read_range.assert_called()
        mock_client.write_range.assert_called()

    def test_acquire_raises_timeout_when_lock_held(self):
        """Test timeout when lock is held by another process."""
        mock_client = MagicMock()
        mock_client.ensure_sheet = MagicMock()
        # Simulate always returning a fresh lock
        now_iso = datetime.now(timezone.utc).isoformat()
        mock_client.read_range = MagicMock(
            return_value=[[f"{now_iso}|9999"]]
        )

        lock_mgr = SyncLockManager(
            mock_client,
            "spreadsheet_id",
            acquire_timeout=0.1,
        )

        with pytest.raises(Exception):  # LockTimeoutError
            lock_mgr.acquire()

    def test_acquire_breaks_stale_lock(self):
        """Test acquiring lock when previous lock is stale."""
        mock_client = MagicMock()
        mock_client.ensure_sheet = MagicMock()

        # First call returns stale lock, second call returns empty
        stale_time = (datetime.now(timezone.utc) - timedelta(seconds=350)).isoformat()
        mock_client.read_range = MagicMock(
            side_effect=[
                [[f"{stale_time}|9999"]],  # Stale lock
                [[""]],  # Cell is now empty
            ]
        )
        mock_client.write_range = MagicMock()

        lock_mgr = SyncLockManager(
            mock_client,
            "spreadsheet_id",
            stale_timeout=300.0,
            acquire_timeout=5.0,
        )

        result = lock_mgr.acquire()

        assert result is True

    def test_release_clears_cell(self):
        """Test that release clears the lock cell."""
        mock_client = MagicMock()
        mock_client.ensure_sheet = MagicMock()
        mock_client.read_range = MagicMock(return_value=[[""]])
        mock_client.write_range = MagicMock()

        lock_mgr = SyncLockManager(mock_client, "spreadsheet_id")
        lock_mgr.acquire()
        lock_mgr.release()

        # Check that write_range was called with empty string
        calls = mock_client.write_range.call_args_list
        last_call = calls[-1]
        assert last_call[0][2] == [[""]]  # Third arg should be [[""]]

    def test_context_manager_releases_on_exception(self):
        """Test that context manager releases lock on exception."""
        mock_client = MagicMock()
        mock_client.ensure_sheet = MagicMock()
        mock_client.read_range = MagicMock(return_value=[[""]])
        mock_client.write_range = MagicMock()

        lock_mgr = SyncLockManager(mock_client, "spreadsheet_id")

        try:
            with lock_mgr:
                raise ValueError("test error")
        except ValueError:
            pass

        # Verify release was called (write_range should have been called twice)
        assert mock_client.write_range.call_count >= 2

    def test_unparseable_lock_treated_as_stale(self):
        """Test that unparseable lock values are treated as stale."""
        mock_client = MagicMock()
        mock_client.ensure_sheet = MagicMock()
        # First call returns garbage, second call returns empty
        mock_client.read_range = MagicMock(
            side_effect=[
                [["garbage_not_a_timestamp"]],
                [[""]],
            ]
        )
        mock_client.write_range = MagicMock()

        lock_mgr = SyncLockManager(
            mock_client,
            "spreadsheet_id",
            acquire_timeout=5.0,
        )

        result = lock_mgr.acquire()

        assert result is True


class TestDataValidator:
    """Tests for data validation and corruption detection."""

    def test_validate_row_valid(self):
        """Test that a valid row passes validation."""
        row = DelinquentRow(
            lease_id=123,
            name="John Doe",
            address="123 Main St",
            phone="555-1234",
            email="john@example.com",
            amount_owed=1500.0,
            date_added="04/23/2026",
        )

        errors = DataValidator.validate_row(row)

        assert len(errors) == 0

    def test_validate_row_negative_lease_id(self):
        """Test that negative lease_id fails validation."""
        row = DelinquentRow(
            lease_id=-1,
            name="John Doe",
            address="123 Main St",
            phone="555-1234",
            email="john@example.com",
            amount_owed=1500.0,
            date_added="04/23/2026",
        )

        errors = DataValidator.validate_row(row)

        assert len(errors) > 0
        assert "lease_id" in errors[0].lower()

    def test_validate_row_negative_amount(self):
        """Test that negative amount fails validation."""
        row = DelinquentRow(
            lease_id=123,
            name="John Doe",
            address="123 Main St",
            phone="555-1234",
            email="john@example.com",
            amount_owed=-100.0,
            date_added="04/23/2026",
        )

        errors = DataValidator.validate_row(row)

        assert len(errors) > 0
        assert "amount_owed" in errors[0].lower()

    def test_validate_row_invalid_date_format(self):
        """Test that invalid date format fails validation."""
        row = DelinquentRow(
            lease_id=123,
            name="John Doe",
            address="123 Main St",
            phone="555-1234",
            email="john@example.com",
            amount_owed=1500.0,
            date_added="2026-04-23",  # Wrong format
        )

        errors = DataValidator.validate_row(row)

        assert len(errors) > 0
        assert "date_added" in errors[0].lower()

    def test_validate_rows_filters_invalid(self):
        """Test that validate_rows filters out invalid rows."""
        rows = [
            DelinquentRow(
                lease_id=123,
                name="Valid Row",
                address="123 Main St",
                phone="555-1234",
                email="valid@example.com",
                amount_owed=1500.0,
                date_added="04/23/2026",
            ),
            DelinquentRow(
                lease_id=-1,  # Invalid
                name="Invalid Row",
                address="456 Oak Ave",
                phone="555-5678",
                email="invalid@example.com",
                amount_owed=2000.0,
                date_added="04/23/2026",
            ),
        ]

        valid_rows, invalid_count = DataValidator.validate_rows(rows)

        assert len(valid_rows) == 1
        assert invalid_count == 1

    def test_compute_checksum_deterministic(self):
        """Test that checksum is deterministic."""
        values = [
            ["123", "John Doe", "1500.00"],
            ["456", "Jane Smith", "2000.00"],
        ]

        checksum1 = DataValidator.compute_checksum(values)
        checksum2 = DataValidator.compute_checksum(values)

        assert checksum1 == checksum2

    def test_compute_checksum_differs_on_value_change(self):
        """Test that checksum changes when values change."""
        values1 = [
            ["123", "John Doe", "1500.00"],
        ]
        values2 = [
            ["123", "John Doe", "1600.00"],  # Different amount
        ]

        checksum1 = DataValidator.compute_checksum(values1)
        checksum2 = DataValidator.compute_checksum(values2)

        assert checksum1 != checksum2

    def test_verify_write_passes_when_equal(self):
        """Test that verify_write passes when checksums match."""
        values = [
            ["123", "John Doe", "1500.00"],
        ]

        # Should not raise
        DataValidator.verify_write(values, values)

    def test_verify_write_raises_on_mismatch(self):
        """Test that verify_write raises on checksum mismatch."""
        expected = [
            ["123", "John Doe", "1500.00"],
        ]
        actual = [
            ["123", "John Doe", "1600.00"],
        ]

        with pytest.raises(DataCorruptionError):
            DataValidator.verify_write(expected, actual)


class TestDataValidationError:
    """Tests for data validation error handling."""

    def test_empty_rows_validation(self):
        """Test validation of empty row list."""
        rows = []

        valid_rows, invalid_count = DataValidator.validate_rows(rows)

        assert len(valid_rows) == 0
        assert invalid_count == 0

    def test_empty_name_validation(self):
        """Test that empty name fails validation."""
        row = DelinquentRow(
            lease_id=123,
            name="",  # Empty
            address="123 Main St",
            phone="555-1234",
            email="john@example.com",
            amount_owed=1500.0,
            date_added="04/23/2026",
        )

        errors = DataValidator.validate_row(row)

        assert len(errors) > 0


class TestChecksumWithMixedTypes:
    """Tests for checksum with mixed value types."""

    def test_checksum_with_floats_and_strings(self):
        """Test checksum with mixed types (floats, strings, None)."""
        values = [
            ["123", "John Doe", 1500.0, None],
            ["456", "Jane Smith", 2000.5, ""],
        ]

        # Should not raise, should be deterministic
        checksum1 = DataValidator.compute_checksum(values)
        checksum2 = DataValidator.compute_checksum(values)

        assert checksum1 == checksum2

    def test_checksum_normalizes_none_to_string(self):
        """Test that None is normalized to string for checksum."""
        values1 = [["123", None, "1500"]]
        values2 = [["123", None, "1500"]]

        checksum1 = DataValidator.compute_checksum(values1)
        checksum2 = DataValidator.compute_checksum(values2)

        assert checksum1 == checksum2
