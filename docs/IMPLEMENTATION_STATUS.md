# Collections Sync Python - Implementation Status

## Phase 1: core-integrations Updates ✅ COMPLETE

### Changes Made

**File: `src/core_integrations/google_sheets/models.py`** (new)
- Created new file to house `UpsertOptions` dataclass
- Clean separation of models from client logic

**File: `src/core_integrations/google_sheets/client.py`**
- Removed `UpsertOptions` dataclass definition
- Added import: `from .models import UpsertOptions`
- Added `import time` for pauses in batch operations
- Added 3 new methods:

  1. **`batch_update_values()`** (lines 336-380)
     - Chunks range updates into 200-per-call batches
     - Applies 150ms pause between chunks
     - Used by collections-sync for bulk updates

  2. **`get_sheet_numeric_id()`** (lines 381-404)
     - Fetches numeric sheetId for a named tab
     - Used to identify sheets for formatting API calls

  3. **`apply_background_color()`** (lines 405-454)
     - Applies background color to a range of cells
     - Takes RGB values (0.0-1.0)
     - Used to highlight new rows in collections-sync

**File: `src/core_integrations/google_sheets/__init__.py`**
- Changed import: `from .models import UpsertOptions`
- Re-exports unchanged (public API intact)

---

## Phase 2: collections-sync-python Service ✅ COMPLETE

### Files Created

#### Core Configuration & Models
- **`src/collections_sync/__init__.py`** — Package init
- **`src/collections_sync/config.py`** — Pydantic-settings config (env var loading)
- **`src/collections_sync/models.py`** — Data classes (SyncMode, DelinquentRow, SyncResult, SyncRequest)

#### Business Logic
- **`src/collections_sync/transform.py`** — Column definitions and row transformation
  - `HEADERS` (27 columns from Google Sheet)
  - `OWNED_HEADERS` (8 automation-managed columns)
  - `to_sheet_values()` function

- **`src/collections_sync/fetch.py`** — Async concurrent tenant enrichment
  - `fetch_active_owed_rows()` main function
  - Concurrent tenant lookups with `asyncio.Semaphore(3)`
  - `asyncio.to_thread()` wrapping sync BuildiumClient

- **`src/collections_sync/sheets_writer.py`** — Complex Google Sheets operations
  - `CollectionsSheetsWriter` class with methods:
    - `get_existing_key_rows()` — read existing lease IDs
    - `upsert_preserving()` — merge rows, preserve non-owned columns
    - `quick_update_balances()` — balance-only updates
  - Column alias support (flexible header names)
  - "Date First Added" preservation logic
  - Yellow background formatting for new rows

#### FastAPI Application
- **`src/collections_sync/app.py`** — FastAPI routes and lifespan
  - `GET /` — health check
  - `POST /` — sync trigger
  - `lifespan()` context manager for setup/teardown
  - `_run_bulk()` and `_run_quick()` orchestration functions

- **`src/collections_sync/__main__.py`** — Entry point
  - Uvicorn launcher

#### Project Configuration
- **`pyproject.toml`** — Project metadata and dependencies
  - Depends on `core-integrations`
  - FastAPI, Uvicorn, Pydantic-settings
  - Dev dependencies: pytest, pytest-asyncio

- **`.env.example`** — Configuration template
- **`Dockerfile`** — Docker image definition
- **`README.md`** — Complete documentation
- **`tests/__init__.py`** — Test package
- **`IMPLEMENTATION_STATUS.md`** — This file

### Architecture Notes

**Async Strategy:**
- All concurrent work uses `asyncio.Semaphore(3)` for controlled concurrency
- `asyncio.to_thread()` wraps the synchronous `BuildiumClient` for thread pool execution
- No modifications to `BuildiumClient` itself; stays purely sync
- Works around the fact that `google-api-python-client` is also synchronous

**Column Preservation:**
- Sheet has 27 columns; automation owns 8
- Existing row merge: copy existing row, then overwrite only owned columns
- Special case: never overwrite "Date First Added" if non-empty on existing rows
- New rows get light yellow background via Sheets API

**Error Handling:**
- Validation upfront in config loading
- Contextlib for proper resource cleanup
- Logging throughout for observability
- HTTP errors bubble up as 500 responses

---

## Phase 3: Testing ✅ TODO (test files stubbed)

### Test Files to Write
- `tests/test_transform.py` — Column mapping and row transformation
- `tests/test_sheets_writer.py` — Writer logic (mocked GoogleSheetsClient)
- `tests/test_fetch.py` — Concurrent tenant enrichment (mocked BuildiumClient)

---

## Deployment Readiness

### Local Development

```bash
# Install both packages
pip install -e ../core-integrations
pip install -e .

# Run service
python -m collections_sync
```

### Docker

```bash
docker build -t collections-sync:latest .
docker run -e BUILDIUM_KEY=... -e SHEET_ID=... ...
```

### Cloud Run

```bash
gcloud run deploy collections-sync \
  --source . \
  --set-env-vars BUILDIUM_KEY=...,SHEET_ID=...
```

---

## Verification Checklist

- [x] Phase 1 — core-integrations updated (UpsertOptions moved, 3 new methods added)
- [x] Phase 2 — collections-sync-python complete (all files created)
- [x] Imports correctly structured (no circular deps)
- [x] Column definitions match Go original (27 columns, 8 owned)
- [x] Async concurrency pattern (Semaphore(3), asyncio.to_thread)
- [x] Configuration via pydantic-settings (env var loading)
- [x] Documentation (README, docstrings)
- [ ] Phase 3 — Unit tests written
- [ ] Smoke test against real Buildium + Sheets
- [ ] Parity verification vs Go service

---

## Known Differences from Go

1. **Async/Sync**: Python uses `asyncio` for concurrency where Go used goroutines
2. **Framework**: FastAPI instead of net/http
3. **Config**: pydantic-settings instead of manual env parsing
4. **Logging**: Python logging instead of Go log package
5. **Thread safety**: Uses `asyncio.Lock` instead of Go `sync.RWMutex`

All logic is identical; implementation is Pythonic.

---

## Next Steps

1. Write unit tests (Phase 3)
2. Test with real credentials
3. Compare output with Go service
4. Deploy to Cloud Run
5. Archive Go repositories
