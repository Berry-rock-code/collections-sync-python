# Collections Sync Python - Project Overview

## Project Summary

**Collections Sync** is a Python service that automates the synchronization of delinquent tenant data from Buildium (property management platform) into a Google Sheets spreadsheet. It's a direct translation of the original Go service, built with Python's modern async stack.

**Primary Purpose:** Track and monitor outstanding tenant balances across properties, with automated enrichment and flexible manual note-taking capabilities.

---

## Architecture

### High-Level Flow

```
Buildium API
    ↓
[Fetch Outstanding Balances] → [Fetch Leases] → [Concurrent Tenant Enrichment]
    ↓
[Transform & Sort by Amount Owed]
    ↓
[Upsert to Google Sheets]
    ↓
[Apply Formatting & Preserve Manual Columns]
```

### Two Sync Modes

1. **Bulk Mode** — Complete data refresh
   - Fetches all outstanding balances from Buildium
   - Fetches all leases
   - Enriches with tenant details (3 concurrent workers, 250ms throttle)
   - Sorts by amount owed (descending)
   - Upserts to Google Sheets with yellow highlighting for new rows

2. **Quick Mode** — Efficient updates
   - Reads existing lease IDs from the sheet
   - Fetches only balances for those specific leases
   - Updates "Amount Owed" and "Last Edited Date" columns only

### Column Management

The automation **owns 8 specific columns** and preserves all others:

**Owned Columns:**
- Date First Added
- Name
- Address
- Phone Number
- Email
- Amount Owed
- Lease ID
- Last Edited Date

**Preserved Columns:** Staff can manually add notes, court dates, payment plans, etc. in any other column without automation overwriting them.

**Special Logic:**
- "Date First Added" is never overwritten on existing rows
- New rows get light yellow background
- Column names support aliases (e.g., "Amount Owed:" or "Amount Owed")
- Lease ID keys are normalized (decimal suffix stripped: "12345.0" → "12345")

---

## Key Files

### Core Service Files

| File | Purpose |
|------|---------|
| [app.py](src/collections_sync/app.py) | FastAPI application, HTTP routes, orchestration |
| [config.py](src/collections_sync/config.py) | Environment variable loading via pydantic-settings |
| [models.py](src/collections_sync/models.py) | Data classes (SyncMode, DelinquentRow, SyncResult, SyncRequest) |
| [transform.py](src/collections_sync/transform.py) | Column definitions, row transformation logic |
| [fetch.py](src/collections_sync/fetch.py) | Async concurrent tenant enrichment, `asyncio.Semaphore(3)` |
| [sheets_writer.py](src/collections_sync/sheets_writer.py) | Google Sheets operations (read/write/format) |
| [__main__.py](src/collections_sync/__main__.py) | Uvicorn entry point |

### Configuration & Deployment

| File | Purpose |
|------|---------|
| [pyproject.toml](pyproject.toml) | Project metadata, dependencies, tool configs |
| [.env.example](.env.example) | Environment variable template |
| [Dockerfile](Dockerfile) | Docker image definition |
| [cloudbuild.yaml](cloudbuild.yaml) | Google Cloud Build configuration |
| [deploy.sh](deploy.sh) | Cloud Run deployment script |

### Documentation & Testing

| File | Purpose |
|------|---------|
| [README.md](README.md) | Complete user & developer documentation |
| [IMPLEMENTATION_STATUS.md](IMPLEMENTATION_STATUS.md) | Implementation checklist & migration notes |
| [DEPLOYMENT.md](DEPLOYMENT.md) | Detailed deployment guide |
| [tests/](tests/) | Unit tests (pytest, pytest-asyncio) |
| [smoke_test.py](smoke_test.py) | Integration test script |

---

## Key Technical Decisions

### Async Concurrency
- **Pattern:** `asyncio.Semaphore(3)` limits concurrent API calls to 3 workers
- **Thread Pool:** `asyncio.to_thread()` wraps synchronous BuildiumClient (no modifications needed to original client)
- **Throttle:** 250ms pause before each tenant API call to avoid rate limiting

### Column Preservation
- Existing rows are copied, then only owned columns are overwritten
- Non-owned columns remain untouched, enabling staff to add notes/dates/plans
- Special handling for "Date First Added" — never overwritten once set

### Retry Strategy
Both BuildiumClient and GoogleSheetsClient implement exponential backoff:
- Max 5 attempts
- 2s base, doubling each retry
- 50% random jitter
- Retries on 429 (rate limit) and 5xx (server error)

### Dependencies
- **FastAPI** — Modern, async-native web framework
- **Uvicorn** — ASGI server
- **pydantic-settings** — Type-safe environment variable loading
- **core-integrations** — Local package with BuildiumClient and GoogleSheetsClient

---

## Quick Start

### Installation
```bash
# Install core-integrations first
pip install -e ../core-integrations

# Install collections-sync
pip install -e .
```

### Configuration
```bash
cp .env.example .env
# Edit .env with your Buildium and Google Sheets credentials
```

### Running Locally
```bash
python -m collections_sync
# Service listens on http://localhost:8080
```

### Triggering Sync
```bash
# Health check
curl http://localhost:8080/

# Bulk sync (full data refresh)
curl -X POST http://localhost:8080/ \
  -H "Content-Type: application/json" \
  -d '{"mode": "bulk", "max_pages": 0, "max_rows": 0}'

# Quick sync (balance update only)
curl -X POST http://localhost:8080/ \
  -H "Content-Type: application/json" \
  -d '{"mode": "quick", "max_pages": 0, "max_rows": 0}'
```

### Response Example
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

---

## Testing & Quality

### Run Tests
```bash
pytest tests/
pytest tests/ --cov=collections_sync
```

### Code Quality Checks
```bash
mypy src/collections_sync/    # Type checking
ruff check src/               # Linting
black src/ tests/             # Code formatting
```

### Smoke Test
```bash
python smoke_test.py
```

---

## Deployment Options

### Docker
```bash
docker build -t collections-sync:latest .
docker run \
  -e BUILDIUM_KEY=... \
  -e BUILDIUM_SECRET=... \
  -e SHEET_ID=... \
  -e WORKSHEET_NAME=... \
  -p 8080:8080 \
  collections-sync:latest
```

### Google Cloud Run
```bash
gcloud run deploy collections-sync \
  --source . \
  --region us-central1 \
  --set-env-vars BUILDIUM_KEY=...,BUILDIUM_SECRET=...,SHEET_ID=...,WORKSHEET_NAME=...
```

Then trigger via Cloud Scheduler or HTTP requests.

---

## Required Environment Variables

| Variable | Purpose | Example |
|----------|---------|---------|
| `BUILDIUM_KEY` | Buildium API client ID | `abc123` |
| `BUILDIUM_SECRET` | Buildium API client secret | `xyz789` |
| `SHEET_ID` | Google Sheets spreadsheet ID | `1aBc2DeF3gHi4jKl5mNoPqRsT6uVwXyZ` |
| `WORKSHEET_NAME` | Sheet tab name | `Collections` |

### Optional Variables
| Variable | Default | Purpose |
|----------|---------|---------|
| `TEST_SHEET_ID` | none | Override SHEET_ID for safe testing |
| `GOOGLE_SHEETS_CREDENTIALS_PATH` | ADC | Path to service account JSON |
| `PORT` | 8080 | Server port |
| `BAL_TIMEOUT` | 30 | Balance fetch timeout (seconds) |
| `LEASE_TIMEOUT` | 30 | Lease fetch timeout (seconds) |
| `TENANT_TIMEOUT` | 5 | Tenant details timeout (seconds) |

---

## Implementation Status

- ✅ **Phase 1** — core-integrations updates (UpsertOptions moved, 3 new methods)
- ✅ **Phase 2** — collections-sync-python service complete
- ✅ **Phase 3** — Unit tests implemented
- ✅ **Smoke test** — Integration test coverage

### Migration from Go

This Python version is a direct translation of the Go service at `/code/BRH/collections-sync`.

**Key Differences:**
- `asyncio` replaces goroutines for concurrency
- `FastAPI` replaces net/http
- `pydantic` replaces Go struct tags
- `BuildiumClient` wrapped with `asyncio.to_thread()` for concurrent calls
- Same column preservation logic, same error handling, same timeouts

---

## Common Issues & Solutions

| Issue | Solution |
|-------|----------|
| "Sheets client is nil" | Check `GOOGLE_SHEETS_CREDENTIALS_PATH` or set `GOOGLE_APPLICATION_CREDENTIALS` |
| "key header not found" | Verify sheet has "Lease ID" column (aliases supported) |
| "Timeout" | Increase timeout envvars or use `max_pages`/`max_rows` to limit data |
| Rate limit errors (429) | Throttle is already 250ms; consider running quick mode more frequently |

---

## Documentation

- **[README.md](README.md)** — Complete user & developer guide
- **[DEPLOYMENT.md](DEPLOYMENT.md)** — Detailed deployment instructions
- **[IMPLEMENTATION_STATUS.md](IMPLEMENTATION_STATUS.md)** — Implementation checklist
- **[src/collections_sync/](src/collections_sync/)** — In-code docstrings

---

## Next Steps (if needed)

1. Run smoke test against real Buildium + Sheets
2. Compare output with Go service for parity
3. Deploy to Cloud Run
4. Monitor logs and adjust timeouts/concurrency as needed
5. Archive Go repositories once migration is verified

