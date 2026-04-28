# Collections Sync — Python

Collections status sync service that syncs delinquent tenant data from Buildium property management into a Google Sheets spreadsheet.

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

Required variables:
- `BUILDIUM_KEY` — Buildium API client ID (or `BUILDIUM_CLIENT_ID`)
- `BUILDIUM_SECRET` — Buildium API client secret (or `BUILDIUM_CLIENT_SECRET`)
- `SHEET_ID` — Google Sheets spreadsheet ID (or `SPREADSHEET_ID`)
- `WORKSHEET_NAME` — Sheet tab name (or `SHEET_TITLE`)

Optional variables:
- `TEST_SHEET_ID` — Override `SHEET_ID` for safe testing
- `BUILDIUM_API_URL` — Buildium API base URL (or `BUILDIUM_BASE_URL`, default `https://api.buildium.com/v1`)
- `GOOGLE_SHEETS_CREDENTIALS_PATH` — Path to service account JSON (or use ADC)
- `PORT` — Server port (default 8080)
- Timeouts and tuning parameters (see `.env.example`)

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

## Architecture

### Sync Modes

**Bulk Mode:**
1. Fetch all outstanding balances from Buildium
2. Fetch all leases from Buildium
3. Concurrently enrich with tenant details (3 workers, 250ms throttle)
4. Sort by amount owed (descending)
5. Upsert to Google Sheets with yellow background for new rows

**Quick Mode:**
1. Read existing lease IDs from sheet
2. Fetch only balances for those leases
3. Update "Amount Owed" and "Last Edited Date" columns only

### Column Management

The automation "owns" 8 specific columns:
- Date First Added
- Name
- Address:
- Phone Number
- Email
- Amount Owed:
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
│   ├── __main__.py          # Entry point
│   ├── app.py               # FastAPI app
│   ├── config.py            # pydantic-settings config
│   ├── models.py            # Data models
│   ├── transform.py         # Column definitions
│   ├── fetch.py             # Concurrent tenant fetching
│   └── sheets_writer.py     # Google Sheets operations
├── tests/
│   ├── test_transform.py
│   ├── test_sheets_writer.py
│   └── test_fetch.py
├── pyproject.toml
├── .env.example
├── Dockerfile
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

### Logging

The service logs to stdout. Set logging level via environment:

```bash
# In production (uvicorn)
--log-level info
```

### Common Issues

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
  "leases_scanned": 2100
}
```

### GET /

Health check. Returns `{"status": "ok"}`.

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
