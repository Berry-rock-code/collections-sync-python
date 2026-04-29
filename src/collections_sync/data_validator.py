"""Row-level validation and write-verification for sheet data."""
import hashlib
import json
import logging
import re
from typing import Any

from .exceptions import DataCorruptionError
from .models import DelinquentRow

logger = logging.getLogger(__name__)

DATE_RE = re.compile(r"^\d{2}/\d{2}/\d{4}$")


class DataValidator:
    """Validate rows and detect corruption."""

    @staticmethod
    def validate_row(row: DelinquentRow) -> list[str]:
        """Validate a single row.

        Args:
            row: A DelinquentRow to validate.

        Returns:
            List of validation error strings (empty = valid).
        """
        errors = []

        if not isinstance(row.lease_id, int) or row.lease_id <= 0:
            errors.append(f"Invalid lease_id: {row.lease_id} (must be positive int)")

        if not isinstance(row.amount_owed, (int, float)):
            errors.append(f"Invalid amount_owed: {row.amount_owed} (must be numeric)")
        elif row.amount_owed < 0:
            errors.append(f"Invalid amount_owed: {row.amount_owed} (must be >= 0)")

        if not row.name or not isinstance(row.name, str):
            errors.append(f"Invalid name: {row.name!r} (must be non-empty string)")
        elif len(row.name) > 200:
            errors.append(f"Invalid name: too long ({len(row.name)} > 200 chars)")

        if row.date_added and not DATE_RE.match(row.date_added):
            errors.append(
                f"Invalid date_added: {row.date_added!r} "
                f"(must match MM/DD/YYYY or be empty)"
            )

        return errors

    @staticmethod
    def validate_rows(rows: list[DelinquentRow]) -> tuple[list[DelinquentRow], int]:
        """Validate multiple rows.

        Args:
            rows: List of DelinquentRow to validate.

        Returns:
            Tuple of (valid_rows, invalid_count).
            Logs each invalid row as a warning but continues.
        """
        valid_rows = []
        invalid_count = 0

        for i, row in enumerate(rows):
            errs = DataValidator.validate_row(row)
            if errs:
                logger.warning(
                    f"Invalid row {i} (lease_id={row.lease_id}): {'; '.join(errs)}"
                )
                invalid_count += 1
            else:
                valid_rows.append(row)

        if invalid_count > 0:
            logger.warning(
                f"Validation complete: {len(valid_rows)} valid, "
                f"{invalid_count} invalid"
            )

        return valid_rows, invalid_count

    @staticmethod
    def compute_checksum(values: list[list[Any]]) -> str:
        """Compute SHA-256 checksum for sheet rows.

        Args:
            values: List of sheet rows from read_range.

        Returns:
            Hex string of SHA-256 hash.
        """
        serialized = json.dumps(
            values, sort_keys=True, separators=(",", ":"), default=str
        )
        return hashlib.sha256(serialized.encode()).hexdigest()

    @staticmethod
    def verify_write(
        expected_values: list[list[Any]],
        actual_values: list[list[Any]],
    ) -> None:
        """Verify that a write operation succeeded as expected.

        Args:
            expected_values: Sheet rows we intended to write.
            actual_values: Sheet rows we read back after writing.

        Raises:
            DataCorruptionError: If checksums do not match.
        """
        expected_checksum = DataValidator.compute_checksum(expected_values)
        actual_checksum = DataValidator.compute_checksum(actual_values)

        # Debug logging: log the actual data for analysis
        logger.debug(f"Checksum verification: expected={expected_checksum}")
        logger.debug(f"Checksum verification: actual={actual_checksum}")
        logger.debug(f"Expected data (first 3 rows): {expected_values[:3]}")
        logger.debug(f"Actual data (first 3 rows): {actual_values[:3]}")

        # Log row-by-row diff for first few rows
        if expected_values and actual_values:
            for i in range(min(3, len(expected_values), len(actual_values))):
                if i < len(expected_values) and i < len(actual_values):
                    exp_row = expected_values[i]
                    act_row = actual_values[i]
                    if exp_row != act_row:
                        logger.debug(
                            f"Row {i} differs:\n"
                            f"  Expected: {exp_row}\n"
                            f"  Actual:   {act_row}"
                        )

        if expected_checksum != actual_checksum:
            raise DataCorruptionError(
                f"Checksum mismatch after write! "
                f"Expected {expected_checksum}, got {actual_checksum}"
            )
