# Collections Sync — Python

Collections status sync service that syncs delinquent tenant data from Buildium property management into a Google Sheets spreadsheet.

**Status:** Production-ready with robustness features (distributed locking, atomic operations, data validation).

## Quick Start

### Prerequisites

- Python 3.11+
- `core-integrations` package installed locally
- Google service account credentials (or ADC)
- Buildium API credentials

### Installation

```bash
# Clone both repos
cd /path/to/code/BRH
git clone ...collections-sync-python
git clone ...core-integrations

# Install core-integrations first
cd core-integrations
pip install -e .

# Install collections-sync
cd ../collections-sync-python
pip install -e ".[dev]"
```

### Configuration

Create a `.env` file in the repo root:

```bash
cp .env.example .env
# Edit .env with your credentials
```

**Required variables:**
- `BUILDIUM_KEY` — Buildium API client ID (or `BUILDIUM_CLIENT_ID`)
- `BUILDIUM_SECRET` — Buildium API client secret (or `BUILDIUM_CLIENT_SECRET`)
- `SHEET_ID` — Google Sheets spreadsheet ID (or `SPREADSHEET_ID`)
- `WORKSHEET_NAME` — Sheet tab name (or `SHEET_TITLE`)

**Optional (robustness features, all default to safe/off):**
- `SYNC_ENABLE_ATOMIC=true` — Enable distributed locking + atomic writes
- `SYNC_VERIFY_CHECKSUMS=true` — Detect post-write corruption via SHA-256
- Other tuning: See `.env.example`

### Running Locally

```bash
python -m collections_sync
```

Service listens on `http://localhost:8080`

### Triggering Sync

**Health check:**
```bash
curl http://localhost:8080/
```

**Bulk sync (full data fetch):**
```bash
curl -X POST http://localhost:8080/ \
  -H "Content-Type: application/json" \
  -d '{"mode": "bulk", "max_pages": 0, "max_rows": 0}'
```

**Quick sync (balance update only):**
```bash
curl -X POST http://localhost:8080/ \
  -H "Content-Type: application/json" \
  -d '{"mode": "quick", "max_pages": 0, "max_rows": 0}'
```

Response is JSON:
```json
{
  "mode": "bulk",
  "existing_keys": 42,
  "rows_prepared": 37,
  "rows_updated": 20,
  "rows_appended": 17,
  "leases_scanned": 2100
}
```

## Data Flow

```
┌────────────┐       ┌───────────────┐       ┌──────────────┐
│ Buildium   │       │ Concurrent    │       │ Google       │
│ API        │──────→│ Enrichment    │──────→│ Sheets       │
│            │       │ (3 workers)   │       │              │
│ Balances   │       │               │       │ Upsert with  │
│ Leases     │       │ • Cache       │       │ locking &    │
│ Tenants    │       │ • Validation  │       │ verification │
└────────────┘       └───────────────┘       └──────────────┘
     ▲                                              ▲
     │                                              │
     └──────────── Lock (optional) ────────────────┘
       _sync_lock tab prevents concurrent writes
```

## Architecture

### Sync Modes

**Bulk Mode:**
1. Fetch all outstanding balances from Buildium
2. Fetch all leases from Buildium
3. Concurrently enrich with tenant details (3 workers, 250ms throttle)
4. Sort by amount owed (descending)
5. Upsert to Google Sheets with optional atomic operations & verification

**Quick Mode:**
1. Read existing lease IDs from sheet
2. Fetch only balances for those leases
3. Update "Amount Owed" and "Last Edited Date" columns only

### Column Management

The automation "owns" 8 specific columns:
- Date First Added
- Name
- Address
- Phone Number
- Email
- Amount Owed
- Lease ID
- Last Edited Date

All other columns are preserved as-is, allowing staff to manually enter notes, court dates, payment plans, etc.

**Key behaviors:**
- "Date First Added" is never overwritten on existing rows
- New rows get a light yellow background
- Column names are flexible (aliases supported): "Address:" or "Address", "Amount Owed:" or "Amount Owed", etc.
- Lease ID keys are normalized (decimal suffix stripped: "12345.0" → "12345")

### Concurrent Tenant Enrichment

Bulk mode uses `asyncio.Semaphore(3)` to limit concurrent API calls:
- 3 workers maximum fetch tenant details simultaneously
- 250ms throttle before each call (spreads requests, avoids 429s)
- Tenant cache prevents duplicate API calls

### Retry & Error Handling

Both the `BuildiumClient` and `GoogleSheetsClient` handle retries with exponential backoff:
- Max 5 attempts
- 2s base backoff, doubling each retry
- 50% random jitter
- Retries on 429 (rate limit) and 5xx (server error) only

## Robustness Features

The service includes three **opt-in** safety layers to prevent data corruption when running in production with multiple instances or scheduled jobs:

### 1. Distributed Locking

**Problem:** Multiple Cloud Run instances or scheduled jobs can trigger syncs simultaneously. Without locking, they read the sheet at T1, both process for 30s, then both write at T2+30s — the second write **overwrites the first**, causing data loss.

**Solution:** A distributed lock stored in a hidden `_sync_lock` tab in the same Google Sheet:

```
┌─────────────────────────────────────────────────────────┐
│ Process A: acquire() → checks _sync_lock!A1            │
│                                                         │
│ Cell empty? YES → Write "2026-04-28T14:32:11Z|12345"  │
│                   (ISO timestamp | process ID)         │
│                → Proceed with sync ✓                   │
└─────────────────────────────────────────────────────────┘
                            ▼
┌─────────────────────────────────────────────────────────┐
│ Process B: acquire() → checks _sync_lock!A1            │
│                                                         │
│ Cell empty? NO → Check age of lock                     │
│ Age < 300s? YES → Wait (poll every 2s, timeout 30s)   │
│ Timeout → raise LockTimeoutError(503)                 │
│                  ("Another sync in progress")          │
└─────────────────────────────────────────────────────────┘
```

**Enable:** Set `SYNC_ENABLE_ATOMIC=true`

### 2. Atomic Upsert with Verification

**Problem:** A single sync can fail mid-write (e.g., chunk 3 of 5 fails due to network timeout). Sheet is left with partial data.

**Solution:** Read → Validate → Plan → Write → Verify pattern:

```
1. Acquire lock (if enabled)
2. Validate input rows (reject: negative lease_id, invalid dates, etc.)
3. Read sheet headers and existing rows
4. Plan updates (pure computation: merge vs. append decision)
5. Compute SHA-256 checksum of expected state
6. Write updates in chunks (max 200 ranges/call, 150ms pause)
7. Write appends
8. Apply formatting (yellow background for new rows)
9. Verify: Read back and compare SHA-256 checksums
   → If match: ✓ Write succeeded
   → If differ: ✗ Raise DataCorruptionError (requires manual intervention)
```

**Enable:** Set `SYNC_ENABLE_ATOMIC=true` and `SYNC_VERIFY_CHECKSUMS=true`

### 3. Data Validation

**Problem:** Invalid source data (negative amounts, malformed dates) pollutes the sheet.

**Solution:** Pre-write validation with non-fatal filtering:

```
Validate each row:
  ✓ lease_id > 0
  ✓ amount_owed >= 0
  ✓ name non-empty
  ✓ date_added matches MM/DD/YYYY (if provided)

Invalid rows are filtered out and logged, sync continues with valid rows only.
```

**Enable:** Automatic (runs whenever atomic upsert is enabled)

### Configuration

```bash
# Enable all robustness features
SYNC_ENABLE_ATOMIC=true
SYNC_VERIFY_CHECKSUMS=true

# Tuning (defaults are safe)
SYNC_LOCK_SHEET=_sync_lock
SYNC_LOCK_TIMEOUT_SECONDS=30         # Wait for lock
SYNC_LOCK_STALE_SECONDS=300          # Force-release stale locks (5 min)
SYNC_WRITE_CHUNK_SIZE=200            # Ranges per batch update call
SYNC_MAX_RETRIES=2                   # Retries on transient failures
SYNC_RETRY_BACKOFF_MS=2000           # Wait between retries
```

All features default to **off**, preserving existing behavior on deploy. No migration needed.

**See also:** `docs/ROBUSTNESS_FEATURES.md` for detailed architecture, execution flows, and migration strategies.

## Development

### Testing

```bash
# Run unit tests
pytest tests/

# Run with coverage
pytest tests/ --cov=collections_sync
```

### Code Quality

```bash
# Type checking
mypy src/collections_sync/

# Linting
ruff check src/

# Formatting
black src/ tests/
```

### Project Structure

```
collections-sync-python/
├── src/collections_sync/
│   ├── __init__.py
│   ├── __main__.py             # Entry point
│   ├── app.py                  # FastAPI app (request handlers)
│   ├── config.py               # pydantic-settings config
│   ├── models.py               # Data models (SyncRequest, SyncResult, etc.)
│   ├── transform.py            # Column definitions & headers
│   ├── fetch.py                # Concurrent tenant enrichment
│   ├── sheets_writer.py        # Google Sheets operations (upsert, atomic)
│   ├── lock_manager.py         # Distributed lock via _sync_lock tab
│   ├── data_validator.py       # Row validation & checksum verification
│   ├── exceptions.py           # Custom exception types
│   └── async_utils.py          # Async helpers (run_sync_with_timeout)
├── tests/
│   ├── test_transform.py
│   ├── test_sheets_writer.py
│   ├── test_fetch.py
│   ├── test_config.py
│   └── test_atomic_operations.py    # Tests for lock, validator, atomic upsert
├── docs/
│   ├── ROBUSTNESS_FEATURES.md  # Detailed robustness architecture
│   ├── DATA_FLOW_VISUAL.md     # Payload transformation flows
│   ├── DATA_FLOW_TRACE.md      # Execution trace examples
│   ├── DEPLOYMENT.md           # Deployment strategies
│   └── ...
├── pyproject.toml
├── .env.example
├── Dockerfile
├── cloudbuild.yaml
├── deploy.sh                   # Cloud Run deployment helper
├── smoke_test.py               # Local smoke test script
└── README.md
```

## Deployment

### Docker

```bash
# Build image
docker build -t collections-sync:latest .

# Run container
docker run \
  -e BUILDIUM_KEY=... \
  -e BUILDIUM_SECRET=... \
  -e SHEET_ID=... \
  -e WORKSHEET_NAME=... \
  -p 8080:8080 \
  collections-sync:latest
```

### Cloud Run

```bash
gcloud run deploy collections-sync \
  --source . \
  --region us-central1 \
  --set-env-vars BUILDIUM_KEY=...,BUILDIUM_SECRET=...,SHEET_ID=...,WORKSHEET_NAME=...
```

Then trigger via Cloud Scheduler or HTTP requests.

## Monitoring & Debugging

### Dual-Mode Error Responses

The service provides two error response formats — choose based on your audience:

**USER MODE** (default):
```bash
curl -X POST http://localhost:8080/ \
  -H "Content-Type: application/json" \
  -d '{"mode": "bulk"}'
```

Response for non-technical users:
```json
{
  "error_type": "LockTimeoutError",
  "request_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "message": "Could not acquire sync lock within 30 seconds",
  "actions": [
    "1. Wait 30-60 seconds and retry",
    "2. If persistent, contact support with request_id"
  ]
}
```

**DEBUG MODE** (append `?debug=true`):
```bash
curl -X POST http://localhost:8080/ \
  -H "Content-Type: application/json" \
  -d '{"mode": "bulk"}' \
  --data-urlencode 'debug=true'
```

Response with full technical details for DevOps:
```json
{
  "error_type": "LockTimeoutError",
  "request_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "http_status": 503,
  "message": "Could not acquire sync lock within 30 seconds",
  "exception_type": "LockTimeoutError",
  "stack_trace": "[full Python stack trace]",
  "technical_info": {
    "reason": "Another sync is currently in progress",
    "lock_sheet": "_sync_lock",
    "lock_timeout_seconds": 30,
    "lock_stale_seconds": 300,
    "spreadsheet_id": "1A2b3C4d5E6f7G8h9I0j"
  }
}
```

Use `request_id` to correlate logs across your observability stack (Stackdriver, DataDog, etc.).

### Common Issues

**"LockTimeoutError" (503)** — Another sync is in progress.
- Response: `{"suggestion": "Retry in 30 seconds"}`
- Check: Is another Cloud Run instance / scheduled job running?
- Fix: Increase `SYNC_LOCK_TIMEOUT_SECONDS` if syncs regularly exceed that window

**"DataValidationError" (422)** — Invalid source data in Buildium.
- Response: Lists which rows failed validation (negative amounts, invalid dates, etc.)
- Fix: Check Buildium data quality; run smoke test to validate enrichment

**"DataCorruptionError" (500)** — Post-write checksum mismatch.
- Response: `{"suggestion": "Verify the sheet manually. Manual intervention required."}`
- Cause: Sheet was modified between write and verify steps (rare)
- Fix: Inspect sheet for partial writes; manually fix and re-run sync
- *Note:* Does NOT retry (sheet state unknown)

**"Sheets client is nil"** — Credentials not loaded. Check:
- `GOOGLE_SHEETS_CREDENTIALS_PATH` points to valid JSON file
- OR `GOOGLE_APPLICATION_CREDENTIALS` env var is set
- OR running on Cloud Run / GCP with ADC available

**"key header not found"** — Sheet header row is missing "Lease ID" column. Check:
- `HEADER_ROW` points to correct row (usually row 1)
- Sheet header names match (with alias support)

**"Timeout"** — Bulk sync taking too long. Options:
- Increase `BAL_TIMEOUT`, `LEASE_TIMEOUT`, `TENANT_TIMEOUT`
- Set `max_pages` or `max_rows` to limit data fetched
- Run `quick` mode instead (only updates balances)

## API Reference

### POST /

**Request:**
```json
{
  "mode": "bulk|quick",
  "max_pages": 0,
  "max_rows": 0
}
```

- `mode`: "bulk" (full sync) or "quick" (balance-only)
- `max_pages`: Max pages of leases to fetch (0 = no limit)
- `max_rows`: Max delinquent rows to return (0 = no limit)

**Response:**
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

- `request_id` — UUID for request tracing (use in logs, error responses)

### GET /

Health check. Returns `{"status": "ok"}`.

## Documentation

For deeper dives, see:

| Doc | Purpose |
|-----|---------|
| [docs/ROBUSTNESS_FEATURES.md](docs/ROBUSTNESS_FEATURES.md) | Detailed architecture of locking, atomic ops, validation; migration strategy |
| [docs/DATA_FLOW_VISUAL.md](docs/DATA_FLOW_VISUAL.md) | Payload transformation flows (HTTP → Buildium → Sheet) |
| [docs/DATA_FLOW_TRACE.md](docs/DATA_FLOW_TRACE.md) | Annotated execution traces showing exactly what happens at each step |
| [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) | Cloud Run & Docker deployment, GCP secrets, monitoring setup |
| [docs/IMPLEMENTATION_STATUS.md](docs/IMPLEMENTATION_STATUS.md) | Feature checklist and test coverage |
| [docs/CORE_INTEGRATIONS_GUIDE.md](docs/CORE_INTEGRATIONS_GUIDE.md) | BuildiumClient and GoogleSheetsClient usage |

## Migration from Go

This is a direct translation of the Go service at `/code/BRH/collections-sync`.

Key differences:
- `asyncio` replaces goroutines for concurrency
- `FastAPI` replaces net/http
- `pydantic` replaces Go struct tags
- `BuildiumClient` is wrapped with `asyncio.to_thread()` for concurrent calls
- Same column preservation logic, same error handling, same timeouts

## License

Internal use only.
