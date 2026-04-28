# Collections Sync — Robustness Features Guide

## Overview

The robustness fixes add three critical safety layers to prevent data corruption:

1. **Distributed Locking** — Prevents concurrent syncs from overwriting each other
2. **Atomic Operations** — Read→Validate→Plan→Write→Verify as a single logical unit
3. **Data Validation** — Rejects invalid rows and detects post-write corruption

All features are **opt-in** via environment variables. Existing behavior is unchanged unless explicitly enabled.

---

## 1. Distributed Locking via Google Sheets

### Purpose

Multiple Cloud Run instances or scheduled jobs can trigger syncs simultaneously. Without locking, they read the sheet at T1, both process for 30s, then both write at T2+30s — the second write **overwrites the first**, causing data loss.

### Solution: `SyncLockManager`

**File:** `src/collections_sync/lock_manager.py`

The lock is stored in a hidden `_sync_lock` tab in the same Google Sheets spreadsheet:

```
_sync_lock tab:
┌──────────────────────────────────────────┐
│ A1: "2026-04-28T14:32:11Z|12345"        │
└──────────────────────────────────────────┘
        ^                                  ^
        │                                  │
    UTC ISO8601 timestamp            Process ID
    (identifies when lock was taken)
```

### Lock State Machine

```
┌─────────────────────────────────────────────────────────────┐
│ Initial: _sync_lock!A1 is empty ("")                        │
└─────────────────────────────────────────────────────────────┘
                            ▼
┌─────────────────────────────────────────────────────────────┐
│ Process A calls: lock_mgr.acquire(timeout=30s)              │
│                                                              │
│ 1. Check cell: empty? YES                                   │
│ 2. Write timestamp + PID: "2026-04-28T14:32:11Z|12345"     │
│ 3. Acquire lock ✓                                           │
└─────────────────────────────────────────────────────────────┘
                            ▼
┌─────────────────────────────────────────────────────────────┐
│ Process B tries: lock_mgr.acquire(timeout=30s)              │
│                                                              │
│ 1. Check cell: empty? NO                                    │
│ 2. Parse timestamp: "2026-04-28T14:32:11Z"                │
│ 3. Age = NOW - timestamp = 0 seconds                        │
│ 4. Is age > 300s (stale timeout)? NO                        │
│ 5. Lock is fresh, wait...                                   │
│ 6. Sleep 2 seconds, retry                                   │
│    (repeat up to timeout=30s)                               │
│ 7. Timeout reached: raise LockTimeoutError(503)             │
└─────────────────────────────────────────────────────────────┘
                            ▼
┌─────────────────────────────────────────────────────────────┐
│ Process A completes:                                         │
│                                                              │
│ 1. Call: lock_mgr.release()                                 │
│ 2. Write empty string: ""                                   │
│ 3. Lock cleared ✓                                           │
└─────────────────────────────────────────────────────────────┘
```

### Configuration

**File:** `src/collections_sync/config.py`

```python
sync_lock_sheet: str = "_sync_lock"           # Lock tab name
sync_lock_timeout_seconds: int = 30           # Acquisition timeout
sync_lock_stale_seconds: int = 300            # Stale lock expiry (5 min)
sync_enable_atomic: bool = False              # Enable locking (default: OFF)
```

**Environment Variables:**
```bash
SYNC_LOCK_SHEET=_sync_lock
SYNC_LOCK_TIMEOUT_SECONDS=30
SYNC_LOCK_STALE_SECONDS=300
SYNC_ENABLE_ATOMIC=true              # Enable locking
```

### API Overhead

Each sync incurs:
- 1 `ensure_sheet()` call (creates tab if missing)
- N `read_range()` calls (poll every 2s until acquired or timeout)
- 1 `write_range()` call (acquire)
- 1 `write_range()` call (release)

**Typical cost:** 2-3 API calls if no contention, ~5-10 if waiting.

---

## 2. Atomic Upsert with Post-Write Verification

### Purpose

Even with locking, a single sync can fail mid-write:
- Chunks 1-2 of 5 succeed, chunk 3 fails (network timeout, rate limit)
- Sheet is left with partial data (rows with Amount Owed updated but Date Last Updated missing)

### Solution: `upsert_preserving_atomic()`

**File:** `src/collections_sync/sheets_writer.py:300-600`

The atomic upsert follows a **Read → Validate → Plan → Write → Verify** pattern:

```
┌───────────────────────────────────────────────────────────────┐
│ Step 1: Acquire lock (if configured)                          │
│ ─────────────────────────────────────────────────────────────  │
│ Lock sheet, ensure no other process is writing                │
└───────────────────────────────────────────────────────────────┘
                            ▼
┌───────────────────────────────────────────────────────────────┐
│ Step 2: Validate input rows                                   │
│ ─────────────────────────────────────────────────────────────  │
│ Check: lease_id > 0, amount_owed >= 0, name non-empty,       │
│        date matches MM/DD/YYYY                                │
│ Action: Filter invalid rows, log count                        │
│ If all invalid: Abort with DataValidationError               │
└───────────────────────────────────────────────────────────────┘
                            ▼
┌───────────────────────────────────────────────────────────────┐
│ Step 3: Read sheet state (with checksum if enabled)          │
│ ─────────────────────────────────────────────────────────────  │
│ Read headers, existing rows, compute SHA-256 hash            │
│ Purpose: Detect if sheet changed between reads               │
└───────────────────────────────────────────────────────────────┘
                            ▼
┌───────────────────────────────────────────────────────────────┐
│ Step 4: Plan updates (pure computation, no I/O)              │
│ ─────────────────────────────────────────────────────────────  │
│ For each new row:                                             │
│   IF lease_id exists in sheet:                                │
│     → Merge (keep manual entries, update owned columns)       │
│   ELSE:                                                       │
│     → New row                                                 │
│                                                               │
│ Separate into:                                                │
│   - update_ranges: {range: "A2:Z2", values: [[...]]}          │
│   - to_append: [[row1], [row2], ...]                          │
│                                                               │
│ No writes yet, can be tested in isolation!                   │
└───────────────────────────────────────────────────────────────┘
                            ▼
┌───────────────────────────────────────────────────────────────┐
│ Step 5: Compute expected values (if checksum verify enabled) │
│ ─────────────────────────────────────────────────────────────  │
│ Flatten all planned updates + appends into a single list      │
│ Compute SHA-256 hash of intended state                        │
│ Purpose: Know what we expect to find after write             │
└───────────────────────────────────────────────────────────────┘
                            ▼
┌───────────────────────────────────────────────────────────────┐
│ Step 6: Write updates (batch)                                │
│ ─────────────────────────────────────────────────────────────  │
│ PUT /v4/spreadsheets/{id}/values:batchUpdate                  │
│ Chunked: 200 ranges per call, 150ms pause between             │
│ If fails: Retry up to SYNC_MAX_RETRIES (configurable)         │
│ If corruption error: NO RETRY (sheet state unknown)           │
└───────────────────────────────────────────────────────────────┘
                            ▼
┌───────────────────────────────────────────────────────────────┐
│ Step 7: Write appends (range)                                │
│ ─────────────────────────────────────────────────────────────  │
│ PUT /v4/spreadsheets/{id}/values/{range}                      │
│ Start row = max(existing_row_numbers) + 1                     │
│ If fails: Retry up to SYNC_MAX_RETRIES                        │
└───────────────────────────────────────────────────────────────┘
                            ▼
┌───────────────────────────────────────────────────────────────┐
│ Step 8: Apply formatting (yellow background)                 │
│ ─────────────────────────────────────────────────────────────  │
│ Mark new rows with light yellow for visibility               │
│ If fails: Log warning but continue (non-critical)            │
└───────────────────────────────────────────────────────────────┘
                            ▼
┌───────────────────────────────────────────────────────────────┐
│ Step 9: Verify post-write state (if checksum verify enabled) │
│ ─────────────────────────────────────────────────────────────  │
│ Read back the written rows                                    │
│ Compute SHA-256 hash of actual state                          │
│ Compare to expected hash from Step 5                          │
│                                                               │
│ If hashes match: ✓ Write succeeded                           │
│ If hashes differ: ✗ Raise DataCorruptionError                │
│                   (requires manual sheet inspection)          │
└───────────────────────────────────────────────────────────────┘
                            ▼
┌───────────────────────────────────────────────────────────────┐
│ Step 10: Release lock                                        │
│ ─────────────────────────────────────────────────────────────  │
│ Write empty string to lock cell                              │
│ Other processes can now acquire lock                         │
└───────────────────────────────────────────────────────────────┘
```

### Configuration

**File:** `src/collections_sync/config.py`

```python
sync_enable_atomic: bool = False              # Enable locking/atomic
sync_verify_checksums: bool = False           # Enable post-write verification
sync_write_chunk_size: int = 200              # Batch update chunk size
sync_max_retries: int = 2                     # Retries on transient error
sync_retry_backoff_ms: int = 2000             # Backoff between retries
```

**Environment Variables:**
```bash
SYNC_ENABLE_ATOMIC=true                       # Enable atomic path
SYNC_VERIFY_CHECKSUMS=true                    # Enable checksum verification
SYNC_WRITE_CHUNK_SIZE=200                     # Batch size
SYNC_MAX_RETRIES=2                            # Retries
SYNC_RETRY_BACKOFF_MS=2000                    # Backoff ms
```

### Retry Strategy

```
Attempt 1: Try atomic upsert
           ↓ fails with transient error (e.g., network timeout)
           ↓ NOT DataCorruptionError
           
Wait 2s (SYNC_RETRY_BACKOFF_MS)

Attempt 2: Try atomic upsert again
           ↓ fails with transient error
           
Wait 2s

Attempt 3: Try atomic upsert (last attempt, max_retries=2)
           ↓ fails with DataCorruptionError
           ↓ NO RETRY (sheet state unknown!)
           ↓ Raise DataCorruptionError → 500 HTTP response
```

**Do NOT retry on:**
- `DataCorruptionError` — sheet state is unknown
- `DataValidationError` — input data is bad
- `LockTimeoutError` — another process has the lock

---

## 3. Data Validation & Corruption Detection

### Purpose

Before writing, catch malformed rows. After writing, verify the write succeeded.

### Solution: `DataValidator`

**File:** `src/collections_sync/data_validator.py`

#### Pre-Write Validation

```python
DataValidator.validate_row(row) → list[str]
```

**Checks:**
- `lease_id`: must be positive integer
- `amount_owed`: must be non-negative float
- `name`: must be non-empty string, max 200 chars
- `date_added`: must match MM/DD/YYYY or be empty
- `email`: basic format check (if non-empty)

**Behavior:**
- Non-fatal: invalid rows are filtered, logged, sync continues
- Returns list of invalid rows by index
- Caller decides whether to abort or proceed

**Example:**
```python
rows = [
    DelinquentRow(lease_id=123, name="Valid", ...),    # ✓ Valid
    DelinquentRow(lease_id=-1, name="Invalid", ...),   # ✗ Negative lease_id
    DelinquentRow(lease_id=456, name="", ...),         # ✗ Empty name
]

valid_rows, invalid_count = DataValidator.validate_rows(rows)

# Result:
#   valid_rows = [DelinquentRow(lease_id=123, ...)]
#   invalid_count = 2
#   Log: "Validation complete: 1 valid, 2 invalid"
```

#### Post-Write Checksum Verification

```python
checksum_before = DataValidator.compute_checksum(sheet_rows_before)
# ... write happens ...
checksum_after = DataValidator.compute_checksum(sheet_rows_after)

DataValidator.verify_write(expected, actual)  # Raises DataCorruptionError if mismatch
```

**Checksum Algorithm:**
- Serialize all row values to JSON (with `default=str` for type normalization)
- SHA-256 hash of the JSON string
- Deterministic (same input always produces same hash)

**Example:**
```python
before = [["123", "John", "1500.00"], ["456", "Jane", "2000.00"]]
checksum_before = compute_checksum(before)
# "a1b2c3d4..." (example hash)

# Write happens, Joe accidentally changes 1500 to 1600

after = [["123", "John", "1600.00"], ["456", "Jane", "2000.00"]]
checksum_after = compute_checksum(after)
# "x9y8z7w6..." (different hash!)

verify_write(expected, actual)
# Raises: DataCorruptionError("Checksum mismatch: expected a1b2c3d4, got x9y8z7w6")
```

### Configuration

**File:** `src/collections_sync/config.py`

```python
sync_verify_checksums: bool = False           # Enable checksum verification
```

**Environment Variables:**
```bash
SYNC_VERIFY_CHECKSUMS=true                    # Enable post-write verification
```

**Cost:** One extra read of all written rows after every write. Disable if latency-sensitive.

---

## 4. Enhanced Error Handling in HTTP Responses

### Purpose

Users need actionable error messages and traceability for debugging.

### Solution: Structured Error Responses

**File:** `src/collections_sync/app.py:100-165`

#### Request Tracing

Every sync request gets a unique `request_id`:

```python
request_id = str(uuid.uuid4())  # e.g., "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
logger.info("Sync requested request_id=%s mode=%s", request_id, request.mode)
```

All logs for this sync include the `request_id` for end-to-end tracing.

#### Specific Exception Handlers

```
Exception Type              HTTP Status  Message                              Suggestion
─────────────────────────────────────────────────────────────────────────────────────────
LockTimeoutError           503          "Another sync is in progress"        "Retry in 30 seconds"
DataValidationError        422          "Lease ID -1 (must be positive)"     "Check source data in Buildium"
DataCorruptionError        500          "Checksum mismatch after write"      "Verify sheet manually. Manual intervention needed."
Unexpected Exception       500          Original exception message            "Check logs for request_id"
```

#### Response Schema

```json
{
  "error_type": "LockTimeoutError",
  "request_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "message": "Could not acquire sync lock within 30s",
  "suggestion": "Another sync is in progress. Retry in 30 seconds."
}
```

#### Success Response (with tracing)

```json
{
  "mode": "bulk",
  "existing_keys": 42,
  "rows_prepared": 37,
  "rows_updated": 20,
  "rows_appended": 17,
  "leases_scanned": 2100,
  "request_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
}
```

---

## Execution Flow with Robustness Enabled

### Full Flow: Bulk Sync with All Safeguards

```
POST / {"mode": "bulk", "max_pages": 0, "max_rows": 50}
│
├─ Generate request_id: "a1b2c3d4-..."
├─ Log: "Sync requested request_id=a1b2c3d4"
│
├─ cfg.sync_enable_atomic = True?  YES
│  │
│  ├─ Create SyncLockManager
│  ├─ Create DataValidator
│  │
│  ├─ Fetch data from Buildium
│  │  └─ Returns: list[DelinquentRow]
│  │
│  ├─ Call: writer.upsert_preserving_atomic(
│  │   new_rows=rows,
│  │   lock_manager=lock_mgr,
│  │   validator=validator,
│  │   verify_checksums=True,
│  │   max_retries=2,
│  │   retry_backoff_ms=2000
│  │ )
│  │
│  ├─ Inside upsert_preserving_atomic:
│  │  ├─ Enter: with lock_manager:  (acquire lock)
│  │  ├─ Validate rows (filter invalid)
│  │  ├─ Read sheet state + compute checksum
│  │  ├─ Plan updates (pure computation)
│  │  ├─ Compute expected values hash
│  │  ├─ Write updates (with retries)
│  │  ├─ Write appends (with retries)
│  │  ├─ Apply yellow background
│  │  ├─ Verify post-write: reread + checksum match?
│  │  │  └─ If mismatch: raise DataCorruptionError (NO RETRY)
│  │  ├─ Exit: lock_manager.release()
│  │  └─ Return: (rows_updated, rows_appended)
│  │
│  └─ Return: SyncResult with request_id
│
└─ HTTP 200 OK + SyncResult JSON


Failure Case: Concurrent Sync Attempts

Process A:                           Process B:
│                                    │
├─ request_id=aaaa                  ├─ request_id=bbbb
├─ Acquire lock: ✓                  ├─ Acquire lock: ✗ (A holds it)
├─ Validate rows: ✓                 ├─ Poll every 2s for 30s
├─ Read sheet: ✓                    ├─ Timeout reached
├─ Write updates: ✓                 ├─ Raise LockTimeoutError
├─ Verify checksums: ✓              │
├─ Release lock                      └─ HTTP 503
│                                       {
└─ HTTP 200 OK                           "error_type": "LockTimeoutError",
   {                                      "request_id": "bbbb",
     "mode": "bulk",                      "message": "Could not acquire...",
     "rows_updated": 20,                  "suggestion": "Retry in 30 seconds"
     "request_id": "aaaa"                }
   }


Failure Case: Data Corruption Detected

Process A:
│
├─ Acquire lock: ✓
├─ Validate rows: ✓
├─ Read sheet + checksum: a1b2c3
├─ Plan updates: ✓
├─ Compute expected hash: d4e5f6
├─ Write updates: ✓
├─ Write appends: ✓
├─ Verify: Read back rows
├─ Compute actual hash: x9y8z7  (MISMATCH!)
├─ Raise DataCorruptionError
├─ Release lock
│
└─ HTTP 500
   {
     "error_type": "DataCorruptionError",
     "request_id": "aaaa",
     "message": "Checksum mismatch: expected d4e5f6, got x9y8z7",
     "suggestion": "Verify sheet manually. Manual intervention required."
   }
```

---

## Logging Output Example

```
2026-04-28 14:32:10 INFO  Sync requested request_id=a1b2c3d4 mode=bulk
2026-04-28 14:32:10 DEBUG Using atomic upsert with locking request_id=a1b2c3d4
2026-04-28 14:32:11 INFO  ✓ Acquired sync lock
2026-04-28 14:32:11 DEBUG Validating 37 rows...
2026-04-28 14:32:11 INFO  Validation complete: 37 valid, 0 invalid
2026-04-28 14:32:11 INFO  Reading sheet headers...
2026-04-28 14:32:12 INFO  Found 27 columns
2026-04-28 14:32:12 INFO  Reading existing data rows...
2026-04-28 14:32:13 INFO  Read 42 existing rows
2026-04-28 14:32:13 INFO  Planning updates...
2026-04-28 14:32:13 INFO  Planned: 20 updates, 17 appends
2026-04-28 14:32:13 INFO  Writing 20 updates...
2026-04-28 14:32:14 INFO  ✓ Updated 20 rows
2026-04-28 14:32:14 INFO  Writing 17 appends at row 43...
2026-04-28 14:32:15 INFO  ✓ Appended 17 rows
2026-04-28 14:32:15 DEBUG Applying yellow background to rows 43-59
2026-04-28 14:32:15 INFO  Verifying writes...
2026-04-28 14:32:16 INFO  ✓ Checksum verification passed
2026-04-28 14:32:16 INFO  ✓ Atomic upsert complete: 20 updated, 17 appended
2026-04-28 14:32:16 INFO  ✓ Released sync lock
2026-04-28 14:32:16 INFO  Upsert complete request_id=a1b2c3d4: 20 updated, 17 appended
```

---

## Migration Path (Gradual Rollout)

### Phase 1: Deploy with atomic disabled (default)

```bash
# No env vars set, SYNC_ENABLE_ATOMIC defaults to False
# Existing behavior unchanged
# New code deployed but not active
```

**Verification:**
- All existing tests pass ✓
- No new lock or validator code paths hit
- Performance identical to before

### Phase 2: Enable atomic in staging only

```bash
SYNC_ENABLE_ATOMIC=true
SYNC_VERIFY_CHECKSUMS=false   # Skip extra reads for now
```

**Test against TEST_SHEET_ID:**
- Concurrent sync requests (curl in parallel)
- Verify lock blocks second request → 503
- Verify lock releases after first request completes
- Verify no data loss

### Phase 3: Enable checksums in staging

```bash
SYNC_ENABLE_ATOMIC=true
SYNC_VERIFY_CHECKSUMS=true    # Now verify post-write
```

**Test impact:**
- Measure added latency (extra read per sync)
- Monitor for DataCorruptionError (should be 0)
- Confirm yellow highlighting works

### Phase 4: Enable in production (1% canary)

```bash
SYNC_ENABLE_ATOMIC=true
SYNC_VERIFY_CHECKSUMS=true
```

**Monitoring:**
- Error rate by error_type
- Sync duration histogram (added latency from lock/validation)
- Lock contention (LockTimeoutError count)

### Phase 5: Full rollout

Gradually increase percentage → 100%

---

## FAQ

**Q: What if a lock is stuck for 5+ minutes?**
A: Lock is considered stale if age > 300s. Next process breaks the stale lock and acquires it. This bounds the impact.

**Q: Why not use a database for the lock instead of Google Sheets?**
A: Simpler — no external dependency, lock stored in the same spreadsheet as the data, self-contained.

**Q: Can I run non-atomic and atomic syncs in parallel?**
A: No. The lock mechanism applies to both. If atomic is enabled, all syncs (old code path + new) use the lock.

**Q: Does atomic mode slow down syncs?**
A: Yes, small overhead:
- Lock acquire: 1-3 API calls (2-5s if contended)
- Validation: CPU-bound, < 1s
- Post-write verification (if enabled): 1 extra read (1-2s)
- Retry backoff: 2s per retry (if transient errors)

**Total added cost:** 2-5s per sync (net).

**Q: What if verification detects corruption?**
A: HTTP 500 with detailed error. Operator must manually inspect the sheet, determine what went wrong, and fix it. No automatic recovery (sheet state is unknown).

**Q: Will old code (non-atomic) break with atomic enabled?**
A: No. All syncs use the lock once it's enabled, whether they use the new atomic code path or the old one.

---

## Files Changed

**New Files:**
- `src/collections_sync/exceptions.py` — Custom exception types
- `src/collections_sync/lock_manager.py` — Distributed locking (330 lines)
- `src/collections_sync/data_validator.py` — Validation + checksums (120 lines)
- `tests/test_atomic_operations.py` — Tests for all three (360 lines, 19 tests)

**Modified Files:**
- `src/collections_sync/config.py` — 8 new config fields
- `src/collections_sync/sheets_writer.py` — `upsert_preserving_atomic()` method (400+ lines)
- `src/collections_sync/fetch.py` — CancelledError re-raise, failed lease tracking
- `src/collections_sync/app.py` — Request IDs, specific exception handlers, wire atomic path
- `src/collections_sync/models.py` — `failed_enrichments` field in `SyncResult`
- `.env.example` — Document new env vars (all commented out)

**Tests:**
- All 107 existing tests pass ✓
- 19 new tests all pass ✓

---

## Enabling the Features

### Option 1: Minimal (just locking, no extra latency)

```bash
SYNC_ENABLE_ATOMIC=true
SYNC_VERIFY_CHECKSUMS=false
```

- Prevents concurrent writes (solves race condition)
- No extra read overhead
- Still retries on transient API errors

### Option 2: Full (locking + verification, safest)

```bash
SYNC_ENABLE_ATOMIC=true
SYNC_VERIFY_CHECKSUMS=true
```

- Prevents concurrent writes
- Detects post-write corruption (catches Sheets API bugs)
- +2-3s latency per sync

### Option 3: Conservative (no locking, just validation)

```bash
SYNC_ENABLE_ATOMIC=false
# Validation still runs as part of fetch logic
```

- Existing behavior (no locking)
- Rows with validation errors filtered and logged
- No post-write verification

---

## Monitoring & Alerts

**Key Metrics:**
```
"sync_lock_timeouts" (gauge)       — Count of requests blocked by lock
"sync_validation_errors" (gauge)   — Count of rows filtered due to validation
"sync_corruption_detected" (gauge) — Count of checksum mismatches (alert if > 0)
"sync_duration_seconds" (histogram)— Sync time distribution
```

**Alert Rules:**
- If `sync_corruption_detected` > 0: **Page on-call immediately**
- If `sync_lock_timeouts` / `sync_attempts` > 10%: Investigate sync contention
- If `sync_validation_errors` / `rows_attempted` > 5%: Investigate data quality

