"""Distributed sync lock using a dedicated Google Sheets tab."""
import logging
import os
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from .exceptions import LockAcquireError, LockTimeoutError

if TYPE_CHECKING:
    from core_integrations.google_sheets import GoogleSheetsClient

logger = logging.getLogger(__name__)

LOCK_CELL = "A1"


class SyncLockManager:
    """Manage distributed locks via Google Sheets."""

    def __init__(
        self,
        client: "GoogleSheetsClient",
        spreadsheet_id: str,
        lock_sheet: str = "_sync_lock",
        acquire_timeout: float = 30.0,
        stale_timeout: float = 300.0,
    ) -> None:
        """Initialize the lock manager.

        Args:
            client: Initialized GoogleSheetsClient.
            spreadsheet_id: The spreadsheet ID.
            lock_sheet: Name of the lock sheet tab (default "_sync_lock").
            acquire_timeout: Max seconds to wait for lock acquisition (default 30s).
            stale_timeout: Seconds before a lock is considered expired (default 300s = 5m).
        """
        self.client = client
        self.spreadsheet_id = spreadsheet_id
        self.lock_sheet = lock_sheet
        self.acquire_timeout = acquire_timeout
        self.stale_timeout = stale_timeout
        self.lock_acquired_at: datetime | None = None

    def acquire(self) -> bool:
        """Try to acquire lock, blocking up to acquire_timeout seconds.

        Returns:
            True if lock acquired.

        Raises:
            LockTimeoutError: If lock cannot be acquired within acquire_timeout.
            LockAcquireError: If Google Sheets API fails.
        """
        start = time.time()

        while time.time() - start < self.acquire_timeout:
            try:
                self._ensure_lock_sheet()
            except Exception as e:
                raise LockAcquireError(
                    f"Failed to ensure lock sheet exists: {e}"
                ) from e

            try:
                current_lock = self._read_lock()
            except Exception as e:
                raise LockAcquireError(f"Failed to read lock: {e}") from e

            if current_lock == "" or self._is_stale(current_lock):
                try:
                    new_lock = self._make_lock_value()
                    self._write_lock(new_lock)
                    logger.info(f"✓ Acquired sync lock")
                    self.lock_acquired_at = datetime.now(timezone.utc)
                    return True
                except Exception as e:
                    raise LockAcquireError(f"Failed to write lock: {e}") from e

            logger.debug("Lock held by another process, retrying in 2s...")
            time.sleep(2)

        logger.error(f"Failed to acquire lock after {self.acquire_timeout}s")
        raise LockTimeoutError(
            f"Could not acquire sync lock within {self.acquire_timeout}s"
        )

    def release(self) -> None:
        """Release the lock by clearing the lock cell.

        Logs but does not raise on failure.
        """
        if not self.lock_acquired_at:
            return

        try:
            self._write_lock("")
            logger.info("✓ Released sync lock")
            self.lock_acquired_at = None
        except Exception as e:
            logger.warning(f"Failed to release lock: {e}")

    def __enter__(self) -> "SyncLockManager":
        """Context manager entry."""
        self.acquire()
        return self

    def __exit__(self, *_) -> None:
        """Context manager exit."""
        self.release()

    def _ensure_lock_sheet(self) -> None:
        """Create the lock sheet tab if it does not exist."""
        self.client.ensure_sheet(self.spreadsheet_id, self.lock_sheet)

    def _read_lock(self) -> str:
        """Read current lock value from lock cell.

        Returns:
            Lock value string, or "" if cell is blank.
        """
        cell_a1 = f"{self.lock_sheet}!{LOCK_CELL}"
        vals = self.client.read_range(self.spreadsheet_id, cell_a1)
        if vals and vals[0] and vals[0][0]:
            return str(vals[0][0]).strip()
        return ""

    def _write_lock(self, value: str) -> None:
        """Write value to the lock cell."""
        cell_a1 = f"{self.lock_sheet}!{LOCK_CELL}"
        self.client.write_range(self.spreadsheet_id, cell_a1, [[value]])

    def _is_stale(self, lock_value: str) -> bool:
        """Check if lock timestamp is older than stale_timeout.

        Lock format: "ISO8601_timestamp|pid"

        Args:
            lock_value: The lock cell value.

        Returns:
            True if timestamp is unparseable or older than stale_timeout.
        """
        try:
            if "|" not in lock_value:
                return True
            timestamp_str = lock_value.split("|")[0]
            lock_time = datetime.fromisoformat(timestamp_str)
            age_seconds = (datetime.now(timezone.utc) - lock_time).total_seconds()
            return age_seconds > self.stale_timeout
        except (ValueError, IndexError, TypeError):
            logger.debug(f"Could not parse lock value {lock_value!r}, treating as stale")
            return True

    def _make_lock_value(self) -> str:
        """Return lock value string: 'UTC_ISO8601|pid'."""
        now_iso = datetime.now(timezone.utc).isoformat()
        pid = os.getpid()
        return f"{now_iso}|{pid}"
