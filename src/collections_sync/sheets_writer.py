"""Google Sheets writer for collections-sync with column preservation."""
import logging
import time
from datetime import datetime
from typing import TYPE_CHECKING, Any

from core_integrations.google_sheets import GoogleSheetsClient

from .data_validator import DataValidator
from .exceptions import DataCorruptionError
from .models import DelinquentRow
from .transform import HEADERS, KEY_HEADER, OWNED_HEADERS, to_sheet_values

if TYPE_CHECKING:
    from .lock_manager import SyncLockManager

logger = logging.getLogger(__name__)

# Column aliases allow the sheet header names to vary slightly
COLUMN_ALIASES: dict[str, list[str]] = {
    "Name": ["Name", "Tenant Name"],
    "Address:": ["Address:", "Address"],
    "Phone Number": ["Phone Number", "Phone"],
    "Email": ["Email", "Email Address"],
    "Amount Owed:": ["Amount Owed:", "Amount Owed", "Balance"],
    "Lease ID": ["Lease ID", "Account Number"],
    "Last Edited Date": ["Last Edited Date", "Date"],
    "Date First Added": ["Date First Added"],
}


class CollectionsSheetsWriter:
    """Write delinquent rows to Google Sheets with column preservation."""

    def __init__(
        self,
        client: GoogleSheetsClient,
        spreadsheet_id: str,
        sheet_title: str,
        header_row: int,
        data_row: int,
        key_header: str = KEY_HEADER,
        owned_headers: set[str] | None = None,
    ) -> None:
        """Initialize the writer.

        Args:
            client: Initialized GoogleSheetsClient.
            spreadsheet_id: The spreadsheet ID.
            sheet_title: The sheet tab name.
            header_row: 1-based header row number.
            data_row: 1-based first data row number.
            key_header: The column that acts as the unique key.
            owned_headers: Columns this automation owns and may overwrite.
        """
        self.client = client
        self.spreadsheet_id = spreadsheet_id
        self.sheet_title = sheet_title
        self.header_row = header_row
        self.data_row = data_row
        self.key_header = key_header
        self.owned_headers = owned_headers or OWNED_HEADERS

    def get_existing_key_rows(self) -> tuple[dict[str, int], list[str]]:
        """Get existing lease IDs and their sheet row numbers.

        Returns:
            Tuple of (key -> row number map, sheet headers).

        Raises:
            ValueError: If validation fails.
            googleapiclient.errors.HttpError: If API call fails.
        """
        self._validate_config()

        # Ensure sheet exists
        self.client.ensure_sheet(self.spreadsheet_id, self.sheet_title)

        # Read sheet headers
        headers, _ = self._read_sheet_headers()

        if not headers:
            return {}, []

        # Find key column
        key_idx = self._find_sheet_index(headers, self.key_header)
        if key_idx < 0:
            return {}, headers

        # Read key column values
        col = _col_letter(key_idx)
        read_a1 = f"{self.sheet_title}!{col}{self.data_row}:{col}50000"
        vals = self.client.read_range(self.spreadsheet_id, read_a1)

        out = {}
        for i, row in enumerate(vals):
            if not row:
                continue
            k = _normalize_lease_id_key(str(row[0]))
            if k and k not in out:
                out[k] = self.data_row + i

        return out, headers

    def upsert_preserving(
        self,
        input_headers: list[str],
        new_rows: list[DelinquentRow],
    ) -> tuple[int, int]:
        """Upsert rows, preserving non-owned columns.

        Reads existing sheet, merges new data into owned columns only,
        preserves manually-entered columns, and applies yellow background
        to new rows.

        Args:
            input_headers: Headers that the input rows follow (usually from HEADERS).
            new_rows: List of DelinquentRow to upsert.

        Returns:
            Tuple of (rows_updated, rows_appended).

        Raises:
            ValueError: If validation fails.
            googleapiclient.errors.HttpError: If API call fails.
        """
        self._validate_config()

        # Ensure sheet exists
        self.client.ensure_sheet(self.spreadsheet_id, self.sheet_title)

        # Read sheet headers
        sheet_headers, num_cols = self._read_sheet_headers()

        if not sheet_headers:
            logger.error(f"No headers found in sheet '{self.sheet_title}' at row {self.header_row}")
            raise ValueError(
                f"header row {self.header_row} is empty. "
                f"Please ensure the sheet '{self.sheet_title}' has headers in row {self.header_row}"
            )

        # Find key column
        key_idx = self._find_sheet_index(sheet_headers, self.key_header)
        if key_idx < 0:
            raise ValueError(f"key header {self.key_header!r} not found")

        # Read all existing data rows
        read_a1 = f"{self.sheet_title}!A{self.data_row}:{_col_letter(num_cols - 1)}50000"
        existing = self.client.read_range(self.spreadsheet_id, read_a1)

        # Build map of existing rows by key
        existing_by_key: dict[str, list[Any]] = {}
        for r in existing:
            norm_row = list(r) + [None] * (num_cols - len(r))
            norm_row = norm_row[:num_cols]

            if key_idx >= len(norm_row):
                continue

            k = _normalize_lease_id_key(str(norm_row[key_idx]))
            if k and k not in existing_by_key:
                existing_by_key[k] = norm_row

        # Build index of input headers
        input_idx: dict[str, int] = {}
        for i, h in enumerate(input_headers):
            nh = _normalize_header(h)
            if nh not in input_idx:
                input_idx[nh] = i

        # Build mapping of canonical column names to (input_idx, sheet_idx)
        owned = set(self.owned_headers) | {self.key_header}
        mapping: dict[str, tuple[int, int]] = {}

        for canonical in owned:
            # Find input index
            in_idx = -1
            if _normalize_header(canonical) in input_idx:
                in_idx = input_idx[_normalize_header(canonical)]
            if in_idx < 0:
                # Try aliases
                aliases = COLUMN_ALIASES.get(canonical, [canonical])
                in_idx = _find_header_index_any(input_headers, aliases)

            # Find output (sheet) index
            out_idx = self._find_sheet_index(sheet_headers, canonical)
            if out_idx < 0:
                raise ValueError(f"sheet missing required column for {canonical!r}")

            mapping[canonical] = (in_idx, out_idx)

        # Convert input rows to sheet-order rows, merging with existing data
        sheet_values = to_sheet_values(new_rows)

        merged: list[list[Any]] = []
        key_to_row_num: dict[str, int] = {}

        # Build key->row mapping from existing
        for i, r in enumerate(existing):
            sheet_row = self.data_row + i
            if key_idx < len(r):
                k = _normalize_lease_id_key(str(r[key_idx]))
                if k:
                    if sheet_row > (key_to_row_num.get(k, 0) or self.data_row - 1):
                        key_to_row_num[k] = sheet_row

        # Merge input rows with existing sheet rows
        for input_row, sheet_row in zip(new_rows, sheet_values):
            # Get key from input
            key_in_idx, key_out_idx = mapping[self.key_header]
            if key_in_idx < 0 or key_in_idx >= len(sheet_row):
                continue

            k = _normalize_lease_id_key(str(sheet_row[key_in_idx]))
            if not k:
                continue

            # Check if this is an existing row
            if k in existing_by_key:
                # Copy existing row and selectively update owned columns
                out_row = list(existing_by_key[k])
            else:
                # New row
                out_row = [None] * num_cols

            # Merge owned column values
            for canonical, (in_idx, out_idx) in mapping.items():
                if in_idx < 0 or in_idx >= len(sheet_row):
                    continue

                # Special case: preserve "Date First Added" for existing rows
                if (
                    k in existing_by_key
                    and canonical.strip().lower() == "date first added"
                ):
                    existing_val = str(out_row[out_idx] or "").strip()
                    if existing_val:
                        continue  # Don't overwrite

                out_row[out_idx] = sheet_row[in_idx]

            merged.append(out_row)

        # Split merged rows into updates and appends
        update_ranges = []
        to_append = []

        for out_row in merged:
            if key_idx >= len(out_row):
                continue

            k = _normalize_lease_id_key(str(out_row[key_idx]))
            if not k:
                continue

            if k in key_to_row_num:
                # Update existing row
                row_num = key_to_row_num[k]
                a1 = f"{self.sheet_title}!{_col_letter(0)}{row_num}:{_col_letter(num_cols - 1)}{row_num}"
                update_ranges.append({"range": a1, "values": [out_row]})
            else:
                # Append new row
                to_append.append(out_row)

        # Apply updates
        rows_updated = 0
        if update_ranges:
            self.client.batch_update_values(
                self.spreadsheet_id,
                update_ranges,
                chunk_size=200,
                pause_ms=150,
            )
            rows_updated = len(update_ranges)

        # Apply appends
        rows_appended = 0
        if to_append:
            if not key_to_row_num:
                start_row = self.data_row
            else:
                start_row = max(key_to_row_num.values()) + 1

            end_row = start_row + len(to_append) - 1
            append_a1 = f"{self.sheet_title}!{_col_letter(0)}{start_row}:{_col_letter(num_cols - 1)}{end_row}"

            self.client.write_range(self.spreadsheet_id, append_a1, to_append)
            rows_appended = len(to_append)

            # Apply light yellow background to new rows
            sheet_id = self.client.get_sheet_numeric_id(
                self.spreadsheet_id, self.sheet_title
            )
            if sheet_id is not None:
                try:
                    self.client.apply_background_color(
                        self.spreadsheet_id,
                        sheet_id,
                        start_row_0indexed=start_row - 1,
                        end_row_exclusive=end_row,
                        num_cols=num_cols,
                        red=1.0,
                        green=0.98,
                        blue=0.8,
                    )
                except Exception as e:
                    logger.warning(
                        "Failed to apply yellow background to new rows: %s", e
                    )

        return rows_updated, rows_appended

    def quick_update_balances(
        self,
        key_to_row: dict[str, int],
        sheet_headers: list[str],
        balances: dict[int, float],
    ) -> int:
        """Quick update: only update Amount Owed and Last Edited Date.

        Args:
            key_to_row: Map from normalized lease ID to sheet row number.
            sheet_headers: The sheet header row.
            balances: Map from lease ID to balance amount.

        Returns:
            Number of rows updated.

        Raises:
            ValueError: If required columns not found.
            googleapiclient.errors.HttpError: If API call fails.
        """
        if not key_to_row:
            return 0

        # Find column indices
        owed_idx = self._find_sheet_index(sheet_headers, "Amount Owed:")
        if owed_idx < 0:
            raise ValueError("sheet missing Amount Owed column")

        date_idx = self._find_sheet_index(sheet_headers, "Last Edited Date")
        if date_idx < 0:
            raise ValueError("sheet missing Last Edited Date column")

        today = datetime.now().strftime("%m/%d/%Y")
        updates = []
        rows_updated = 0

        for k, row_num in key_to_row.items():
            try:
                lease_id = int(_normalize_lease_id_key(k))
            except (ValueError, TypeError):
                continue

            if lease_id <= 0:
                continue

            if lease_id not in balances:
                continue

            bal = balances[lease_id]

            owed_a1 = f"{self.sheet_title}!{_col_letter(owed_idx)}{row_num}"
            date_a1 = f"{self.sheet_title}!{_col_letter(date_idx)}{row_num}"

            updates.append({"range": owed_a1, "values": [[bal]]})
            updates.append({"range": date_a1, "values": [[today]]})
            rows_updated += 1

        if updates:
            self.client.batch_update_values(
                self.spreadsheet_id,
                updates,
                chunk_size=200,
                pause_ms=150,
            )

        return rows_updated

    def upsert_preserving_atomic(
        self,
        new_rows: list[DelinquentRow],
        lock_manager: "SyncLockManager | None" = None,
        validator: DataValidator | None = None,
        verify_checksums: bool = False,
        max_retries: int = 0,
        retry_backoff_ms: int = 2000,
    ) -> tuple[int, int]:
        """Atomic upsert with locking, validation, and post-write verification.

        Args:
            new_rows: List of DelinquentRow to upsert.
            lock_manager: Optional lock manager for distributed locking.
            validator: Optional validator for row validation and checksum verification.
            verify_checksums: If True, verify post-write checksums.
            max_retries: Number of retries on non-corruption errors (default 0).
            retry_backoff_ms: Backoff between retries in milliseconds (default 2000).

        Returns:
            Tuple of (rows_updated, rows_appended).

        Raises:
            ValueError: If validation fails.
            DataCorruptionError: If post-write verification fails.
            LockTimeoutError: If lock cannot be acquired.
            googleapiclient.errors.HttpError: If API call fails.
        """
        self._validate_config()

        # Acquire lock if provided
        if lock_manager:
            with lock_manager:
                return self._do_atomic_upsert(
                    new_rows,
                    validator,
                    verify_checksums,
                    max_retries,
                    retry_backoff_ms,
                )
        else:
            return self._do_atomic_upsert(
                new_rows, validator, verify_checksums, max_retries, retry_backoff_ms
            )

    def _do_atomic_upsert(
        self,
        new_rows: list[DelinquentRow],
        validator: DataValidator | None,
        verify_checksums: bool,
        max_retries: int,
        retry_backoff_ms: int,
    ) -> tuple[int, int]:
        """Execute atomic upsert with retries."""
        last_error: Exception | None = None

        for attempt in range(max_retries + 1):
            try:
                return self._execute_upsert(
                    new_rows, validator, verify_checksums
                )
            except DataCorruptionError:
                raise  # Don't retry corruption errors
            except Exception as e:
                last_error = e
                if attempt < max_retries:
                    logger.warning(
                        f"Upsert failed on attempt {attempt + 1}, "
                        f"retrying in {retry_backoff_ms}ms: {e}"
                    )
                    time.sleep(retry_backoff_ms / 1000.0)
                else:
                    raise

        assert last_error is not None
        raise last_error

    def _execute_upsert(
        self,
        new_rows: list[DelinquentRow],
        validator: DataValidator | None,
        verify_checksums: bool,
    ) -> tuple[int, int]:
        """Execute a single atomic upsert attempt."""
        # Step 1: Validate rows if validator provided
        if validator:
            logger.info(f"Validating {len(new_rows)} rows...")
            new_rows, invalid_count = validator.validate_rows(new_rows)
            if invalid_count > 0:
                logger.warning(f"Dropped {invalid_count} invalid rows")

        if not new_rows:
            logger.info("No valid rows to upsert")
            return 0, 0

        # Step 2: Ensure sheet exists
        logger.debug("Ensuring sheet exists...")
        self.client.ensure_sheet(self.spreadsheet_id, self.sheet_title)

        # Step 3: Read sheet headers and existing data
        logger.info("Reading sheet headers...")
        sheet_headers, num_cols = self._read_sheet_headers()

        if not sheet_headers:
            raise ValueError(
                f"header row {self.header_row} is empty. "
                f"Please ensure the sheet '{self.sheet_title}' has headers in row {self.header_row}"
            )

        logger.info(f"Found {num_cols} columns")

        # Step 4: Read all existing data rows
        logger.info("Reading existing data rows...")
        read_a1 = (
            f"{self.sheet_title}!A{self.data_row}:"
            f"{_col_letter(num_cols - 1)}50000"
        )
        existing = self.client.read_range(self.spreadsheet_id, read_a1)
        logger.info(f"Read {len(existing)} existing rows")

        # Step 5: Plan updates (pure computation, no I/O)
        logger.info("Planning updates...")
        update_ranges, to_append, key_to_row_num = self._plan_updates(
            new_rows, sheet_headers, num_cols, existing
        )
        logger.info(
            f"Planned: {len(update_ranges)} updates, {len(to_append)} appends"
        )

        # Step 6: Compute checksum of intended writes if verification enabled
        expected_written_values: list[list[Any]] | None = None
        if verify_checksums and validator:
            expected_written_values = self._compute_expected_values(
                update_ranges, to_append, num_cols
            )

        # Step 7: Write updates
        rows_updated = 0
        if update_ranges:
            logger.info(f"Writing {len(update_ranges)} updates...")
            self.client.batch_update_values(
                self.spreadsheet_id,
                update_ranges,
                chunk_size=200,
                pause_ms=150,
            )
            rows_updated = len(update_ranges)
            logger.info(f"✓ Updated {rows_updated} rows")

        # Step 8: Write appends
        rows_appended = 0
        if to_append:
            if not key_to_row_num:
                start_row = self.data_row
            else:
                start_row = max(key_to_row_num.values()) + 1

            end_row = start_row + len(to_append) - 1
            append_a1 = (
                f"{self.sheet_title}!{_col_letter(0)}{start_row}:"
                f"{_col_letter(num_cols - 1)}{end_row}"
            )

            logger.info(f"Writing {len(to_append)} appends at row {start_row}...")
            self.client.write_range(self.spreadsheet_id, append_a1, to_append)
            rows_appended = len(to_append)
            logger.info(f"✓ Appended {rows_appended} rows")

            # Apply light yellow background to new rows
            sheet_id = self.client.get_sheet_numeric_id(
                self.spreadsheet_id, self.sheet_title
            )
            if sheet_id is not None:
                try:
                    logger.debug(f"Applying yellow background to rows {start_row}-{end_row}")
                    self.client.apply_background_color(
                        self.spreadsheet_id,
                        sheet_id,
                        start_row_0indexed=start_row - 1,
                        end_row_exclusive=end_row,
                        num_cols=num_cols,
                        red=1.0,
                        green=0.98,
                        blue=0.8,
                    )
                except Exception as e:
                    logger.warning(
                        "Failed to apply yellow background to new rows: %s", e
                    )

        # Step 9: Verify writes if enabled
        if verify_checksums and validator and expected_written_values:
            logger.info("Verifying writes...")
            # Re-read only the rows we wrote (updates + appends)
            actual_written = []

            # Read back updated rows
            if update_ranges:
                for update in update_ranges:
                    verify_a1 = update["range"]
                    rows = self.client.read_range(self.spreadsheet_id, verify_a1)
                    actual_written.extend(rows)

            # Read back appended rows
            if to_append:
                if not key_to_row_num:
                    start_row = self.data_row
                else:
                    start_row = max(key_to_row_num.values()) + 1

                end_row = start_row + len(to_append) - 1
                verify_a1 = (
                    f"{self.sheet_title}!A{start_row}:"
                    f"{_col_letter(num_cols - 1)}{end_row}"
                )
                rows = self.client.read_range(self.spreadsheet_id, verify_a1)
                actual_written.extend(rows)

            validator.verify_write(expected_written_values, actual_written)
            logger.info("✓ Checksum verification passed")

        logger.info(
            f"✓ Atomic upsert complete: "
            f"{rows_updated} updated, {rows_appended} appended"
        )
        return rows_updated, rows_appended

    def _plan_updates(
        self,
        new_rows: list[DelinquentRow],
        sheet_headers: list[str],
        num_cols: int,
        existing: list[list[Any]],
    ) -> tuple[list[dict[str, Any]], list[list[Any]], dict[str, int]]:
        """Plan updates and appends (pure computation, no I/O).

        Args:
            new_rows: Input rows to upsert.
            sheet_headers: Actual sheet headers.
            num_cols: Number of columns in sheet.
            existing: Existing data rows from sheet.

        Returns:
            Tuple of (update_ranges, rows_to_append, key_to_row_num).
        """
        # Find key column
        key_idx = self._find_sheet_index(sheet_headers, self.key_header)
        if key_idx < 0:
            raise ValueError(f"key header {self.key_header!r} not found")

        # Build index of input headers (using HEADERS for compatibility)
        input_idx: dict[str, int] = {}
        for i, h in enumerate(HEADERS):
            nh = _normalize_header(h)
            if nh not in input_idx:
                input_idx[nh] = i

        # Build mapping of canonical column names to (input_idx, sheet_idx)
        owned = set(self.owned_headers) | {self.key_header}
        mapping: dict[str, tuple[int, int]] = {}

        for canonical in owned:
            # Find input index
            in_idx = -1
            if _normalize_header(canonical) in input_idx:
                in_idx = input_idx[_normalize_header(canonical)]
            if in_idx < 0:
                # Try aliases
                aliases = COLUMN_ALIASES.get(canonical, [canonical])
                in_idx = _find_header_index_any(HEADERS, aliases)

            # Find output (sheet) index
            out_idx = self._find_sheet_index(sheet_headers, canonical)
            if out_idx < 0:
                raise ValueError(f"sheet missing required column for {canonical!r}")

            mapping[canonical] = (in_idx, out_idx)

        # Convert input rows to sheet-order rows
        sheet_values = to_sheet_values(new_rows)

        # Build existing rows by key
        existing_by_key: dict[str, list[Any]] = {}
        for r in existing:
            norm_row = list(r) + [None] * (num_cols - len(r))
            norm_row = norm_row[:num_cols]

            if key_idx >= len(norm_row):
                continue

            k = _normalize_lease_id_key(str(norm_row[key_idx]))
            if k and k not in existing_by_key:
                existing_by_key[k] = norm_row

        # Build key->row mapping from existing
        key_to_row_num: dict[str, int] = {}
        for i, r in enumerate(existing):
            sheet_row = self.data_row + i
            if key_idx < len(r):
                k = _normalize_lease_id_key(str(r[key_idx]))
                if k:
                    if sheet_row > (key_to_row_num.get(k, 0) or self.data_row - 1):
                        key_to_row_num[k] = sheet_row

        # Merge input rows with existing sheet rows
        merged: list[list[Any]] = []

        for input_row, sheet_row in zip(new_rows, sheet_values):
            # Get key from input
            key_in_idx, key_out_idx = mapping[self.key_header]
            if key_in_idx < 0 or key_in_idx >= len(sheet_row):
                continue

            k = _normalize_lease_id_key(str(sheet_row[key_in_idx]))
            if not k:
                continue

            # Check if this is an existing row
            if k in existing_by_key:
                # Copy existing row and selectively update owned columns
                out_row = list(existing_by_key[k])
            else:
                # New row
                out_row = [None] * num_cols

            # Merge owned column values
            for canonical, (in_idx, out_idx) in mapping.items():
                if in_idx < 0 or in_idx >= len(sheet_row):
                    continue

                # Special case: preserve "Date First Added" for existing rows
                if (
                    k in existing_by_key
                    and canonical.strip().lower() == "date first added"
                ):
                    existing_val = str(out_row[out_idx] or "").strip()
                    if existing_val:
                        continue  # Don't overwrite

                out_row[out_idx] = sheet_row[in_idx]

            merged.append(out_row)

        # Split merged rows into updates and appends
        update_ranges = []
        to_append = []

        for out_row in merged:
            if key_idx >= len(out_row):
                continue

            k = _normalize_lease_id_key(str(out_row[key_idx]))
            if not k:
                continue

            if k in key_to_row_num:
                # Update existing row
                row_num = key_to_row_num[k]
                a1 = (
                    f"{self.sheet_title}!{_col_letter(0)}{row_num}:"
                    f"{_col_letter(num_cols - 1)}{row_num}"
                )
                update_ranges.append({"range": a1, "values": [out_row]})
            else:
                # Append new row
                to_append.append(out_row)

        return update_ranges, to_append, key_to_row_num

    def _compute_expected_values(
        self,
        update_ranges: list[dict[str, Any]],
        to_append: list[list[Any]],
        num_cols: int,
    ) -> list[list[Any]]:
        """Compute the expected sheet values after writing.

        Used for checksum verification.

        Args:
            update_ranges: List of update ranges from _plan_updates.
            to_append: List of rows to append from _plan_updates.
            num_cols: Number of columns in sheet.

        Returns:
            List of all row values that will be written.
        """
        all_values = []
        # Include updates
        for update in update_ranges:
            all_values.extend(update["values"])
        # Include appends
        all_values.extend(to_append)
        return all_values

    def _validate_config(self) -> None:
        """Validate configuration.

        Raises:
            ValueError: If configuration is invalid.
        """
        if not self.sheet_title:
            raise ValueError("sheet_title required")
        if self.header_row <= 0 or self.data_row <= 0 or self.data_row <= self.header_row:
            raise ValueError("invalid header_row or data_row")
        if not self.key_header.strip():
            raise ValueError("key_header required")

    def _read_sheet_headers(self) -> tuple[list[str], int]:
        """Read and parse sheet headers.

        Returns:
            Tuple of (header strings, number of columns).

        Raises:
            googleapiclient.errors.HttpError: If API call fails.
        """
        read_a1 = f"{self.sheet_title}!A{self.header_row}:ZZ{self.header_row}"
        vals = self.client.read_range(self.spreadsheet_id, read_a1)

        if not vals or not vals[0]:
            logger.error(f"Failed to read headers from {read_a1}. API returned: {vals}")
            return [], 0

        raw = vals[0]
        headers = [str(cell).strip() if cell is not None else "" for cell in raw]

        # Find last non-empty header
        last = -1
        for i in range(len(headers) - 1, -1, -1):
            if headers[i].strip():
                last = i
                break

        if last < 0:
            logger.error(f"No headers found in row {self.header_row}. Raw values: {raw}")
            return [], 0

        result = headers[: last + 1], last + 1
        logger.info(f"Read {len(result[0])} headers from row {self.header_row}: {result[0][:5]}...")
        return result

    def _find_sheet_index(self, sheet_headers: list[str], canonical: str) -> int:
        """Find column index in sheet headers using aliases.

        Args:
            sheet_headers: List of sheet header names.
            canonical: The canonical column name.

        Returns:
            0-based column index, or -1 if not found.
        """
        if not sheet_headers:
            return -1

        canonical = canonical.strip()

        # Direct lookup with aliases
        if canonical in COLUMN_ALIASES:
            return _find_header_index_any(sheet_headers, COLUMN_ALIASES[canonical])

        # Normalize and check against all aliases
        nc = _normalize_header(canonical)
        for k, aliases in COLUMN_ALIASES.items():
            if _normalize_header(k) == nc:
                return _find_header_index_any(sheet_headers, aliases)

        # Fallback: try exact match
        return _find_header_index_any(sheet_headers, [canonical])


def _normalize_header(s: str) -> str:
    """Normalize a header string for comparison.

    Lowercases, strips whitespace, collapses multiple spaces.
    """
    s = s.replace("\n", " ")
    s = s.strip().lower()
    s = " ".join(s.split())
    return s


def _normalize_lease_id_key(s: str) -> str:
    """Normalize a lease ID key.

    Strips whitespace and decimal suffix (e.g. "12345.0" -> "12345").
    """
    s = s.strip()
    if not s:
        return ""
    if "." in s:
        s = s.split(".")[0]
    return s.strip()


def _find_header_index_any(headers: list[str], candidates: list[str]) -> int:
    """Find the index of any matching header.

    Args:
        headers: List of header strings.
        candidates: List of candidate names to match (any one).

    Returns:
        0-based index, or -1 if not found.
    """
    if not headers or not candidates:
        return -1

    norm_headers = [_normalize_header(h) for h in headers]

    for cand in candidates:
        want = _normalize_header(cand)
        if not want:
            continue
        for i, h in enumerate(norm_headers):
            if h == want:
                return i

    return -1


def _col_letter(zero_based_idx: int) -> str:
    """Convert 0-based column index to A1 letter notation.

    Examples:
        0 -> A
        25 -> Z
        26 -> AA
    """
    idx = zero_based_idx + 1
    letters = ""
    while idx > 0:
        idx -= 1
        letters = chr(65 + (idx % 26)) + letters
        idx //= 26
    return letters
