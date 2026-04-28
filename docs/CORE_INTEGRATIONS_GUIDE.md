# Core-Integrations Integration Guide

## Overview

**core-integrations** is a shared Python library that provides reusable client wrappers for three external APIs:
1. **Buildium** — Property management platform API
2. **Google Sheets** — Spreadsheet operations
3. **Salesforce** — CRM platform (not used by collections-sync)

Collections-sync-python **depends on core-integrations** as its primary bridge to Buildium and Google Sheets APIs. It doesn't call these APIs directly; instead, it uses the clients provided by core-integrations.

---

## Architecture: How They Connect

```
collections-sync-python                 core-integrations
──────────────────────────────────────  ──────────────────────────────────
    FastAPI app                             BuildiumClient
    ├── fetch.py                            └── fetch_outstanding_balances()
    │   └── uses BuildiumClient              fetch_outstanding_balances_for_lease_ids()
    │       └── get_tenant_details()         list_all_leases()
    │       └── fetch_outstanding_balances() get_tenant_details()
    │                                        [+ retry/backoff logic]
    ├── sheets_writer.py
    │   └── uses GoogleSheetsClient          GoogleSheetsClient
    │       └── read_range()                 ├── read_range()
    │       └── upsert_rows()                ├── upsert_rows()
    │       └── batch_update_values()        ├── batch_update_values()
    │       └── apply_background_color()     ├── apply_background_color()
    │                                        ├── get_sheet_numeric_id()
    │                                        └── [+ A1 notation utilities]
    └── app.py
        └── orchestrates the above
```

---

## Key Classes & Methods

### BuildiumClient (from core-integrations)

**Purpose:** Synchronous HTTP client for the Buildium REST API with automatic retry/backoff.

**Location:** `/home/jake/code/BRH/core-integrations/src/core_integrations/buildium/client.py`

#### Key Methods Used by collections-sync

| Method | Purpose | Returns |
|--------|---------|---------|
| `fetch_outstanding_balances()` | Get all balances owed across all leases | `dict[int, float]` — lease_id → balance |
| `fetch_outstanding_balances_for_lease_ids(lease_ids)` | Get balances for specific leases (chunked) | `dict[int, float]` |
| `list_all_leases(max_pages=0)` | Fetch all leases with optional pagination | `list[Lease]` |
| `get_tenant_details(tenant_id)` | Fetch full tenant profile (name, phone, email) | `TenantDetails` |

#### Example Usage (from collections-sync `fetch.py`)

```python
from core_integrations.buildium import BuildiumClient, BuildiumConfig

config = BuildiumConfig()  # Reads BUILDIUM_* env vars
client = BuildiumClient(config)

# Get all outstanding balances
balances = client.fetch_outstanding_balances()  # {123: 5000.50, 456: 1200.00}

# Get balances for specific leases
balances = client.fetch_outstanding_balances_for_lease_ids([123, 456])

# Get all leases
leases = client.list_all_leases(max_pages=10)  # Returns Lease objects with tenants

# Enrich with tenant details
tenant = client.get_tenant_details(tenant_id=5678)  # TenantDetails(first_name, last_name, email, phone, ...)
```

#### Reliability Features

- **Exponential backoff with jitter** — 2s base, doubling each retry
- **Max 5 attempts** — retries on 429 (rate limit) and 5xx errors
- **Configurable timeouts** — connect/read/write/pool timeouts

---

### GoogleSheetsClient (from core-integrations)

**Purpose:** Comprehensive Google Sheets API v4 client with range operations and upsert support.

**Location:** `/home/jake/code/BRH/core-integrations/src/core_integrations/google_sheets/client.py`

#### Key Methods Used by collections-sync

| Method | Purpose | Returns |
|--------|---------|---------|
| `read_range(sheet_id, a1_range)` | Read values from a range (e.g., "Collections!A1:Z1000") | `list[list[Any]]` |
| `upsert_rows(sheet_id, opts, rows)` | Intelligent row upsert by key column | None (modifies sheet in-place) |
| `batch_update_values(sheet_id, updates)` | **NEW** — Batch update ranges in chunks | None |
| `get_sheet_numeric_id(sheet_id, sheet_name)` | **NEW** — Get numeric sheetId for a named tab | `int` |
| `apply_background_color(sheet_id, sheet_id_num, range, color)` | **NEW** — Apply background color to a range | None |

#### Example Usage (from collections-sync `sheets_writer.py`)

```python
from core_integrations.google_sheets import GoogleSheetsClient, GoogleSheetsConfig, UpsertOptions

config = GoogleSheetsConfig()  # Reads GOOGLE_SHEETS_* env vars
client = GoogleSheetsClient(config)

sheet_id = "1abc2def3ghi4jkl5mno..."
sheet_name = "Collections"

# Read existing data
existing_rows = client.read_range(sheet_id, f"{sheet_name}!A:Z")

# Upsert rows with intelligent merge
opts = UpsertOptions(
    sheet_title=sheet_name,
    header_row=1,
    data_row=2,
    key_header="Lease ID",
    ensure_headers=True,
    headers=["Lease ID", "Name", "Amount Owed", ...],
    num_columns=8
)
client.upsert_rows(sheet_id, opts, rows_to_upsert)

# Batch update multiple ranges
updates = [
    {"range": f"{sheet_name}!A2:A100", "values": [[val] for val in col_a]},
    {"range": f"{sheet_name}!D2:D100", "values": [[val] for val in col_d]},
]
client.batch_update_values(sheet_id, updates)

# Get numeric sheet ID for formatting API
numeric_id = client.get_sheet_numeric_id(sheet_id, sheet_name)

# Apply yellow background to new rows
yellow = {"red": 1.0, "green": 1.0, "blue": 0.0}  # RGB 0.0-1.0
client.apply_background_color(sheet_id, numeric_id, "Collections!A2:Z100", yellow)
```

#### Reliability Features

- **Flexible authentication** — Service account file OR Application Default Credentials
- **Case-insensitive header lookup** — Column names are normalized
- **A1 notation utilities** — Column/row conversion helpers
- **Automatic sheet creation** — `ensure_sheet()` creates missing sheets

---

## Three New Methods Added to GoogleSheetsClient

These methods were added in Phase 1 to support collections-sync:

### 1. `batch_update_values()`

**Purpose:** Update multiple ranges efficiently, chunking large updates to avoid API limits.

**Signature:**
```python
def batch_update_values(self, spreadsheet_id: str, updates: list[dict[str, Any]]) -> None:
    """
    Args:
        spreadsheet_id: Google Sheets ID
        updates: List of {"range": "Sheet!A1:Z10", "values": [[...]]}
    
    Chunks updates into batches of 200 per API call with 150ms pause between chunks.
    """
```

**Used by:** `sheets_writer.py:CollectionsSheetsWriter.upsert_preserving()` for bulk updates.

### 2. `get_sheet_numeric_id()`

**Purpose:** Fetch the numeric sheet ID needed for the Sheets API formatting endpoints.

**Signature:**
```python
def get_sheet_numeric_id(self, spreadsheet_id: str, sheet_name: str) -> int:
    """
    Args:
        spreadsheet_id: Google Sheets ID
        sheet_name: Name of the tab (e.g., "Collections")
    
    Returns: Numeric sheetId (used for formatting API calls)
    """
```

**Used by:** `sheets_writer.py:CollectionsSheetsWriter.upsert_preserving()` to highlight new rows.

### 3. `apply_background_color()`

**Purpose:** Apply background color to a range of cells via the batchUpdate API.

**Signature:**
```python
def apply_background_color(
    self, 
    spreadsheet_id: str, 
    sheet_numeric_id: int, 
    a1_range: str, 
    color: dict[str, float]
) -> None:
    """
    Args:
        spreadsheet_id: Google Sheets ID
        sheet_numeric_id: Numeric sheetId (from get_sheet_numeric_id())
        a1_range: Range in A1 notation (e.g., "Collections!A2:Z10")
        color: RGB color dict {"red": 0.0-1.0, "green": 0.0-1.0, "blue": 0.0-1.0}
    
    Example: Apply light yellow to new rows
        yellow = {"red": 1.0, "green": 1.0, "blue": 0.0}
        client.apply_background_color(sheet_id, numeric_id, "Sheet!A2:Z100", yellow)
    """
```

**Used by:** `sheets_writer.py:CollectionsSheetsWriter.upsert_preserving()` to highlight new rows with yellow.

---

## Configuration & Environment Variables

### core-integrations Configuration

Both clients read from environment variables via pydantic-settings:

#### BuildiumConfig (prefix: `BUILDIUM_`)

| Variable | Required | Default | Used by |
|----------|----------|---------|---------|
| `BUILDIUM_KEY` or `BUILDIUM_CLIENT_ID` | ✅ | — | BuildiumClient auth header |
| `BUILDIUM_SECRET` or `BUILDIUM_CLIENT_SECRET` | ✅ | — | BuildiumClient auth header |
| `BUILDIUM_API_URL` or `BUILDIUM_BASE_URL` | ❌ | `https://api.buildium.com/v1` | API endpoint |
| `BUILDIUM_PAGE_SIZE` | ❌ | `1000` | Pagination |
| `BUILDIUM_CONNECT_TIMEOUT_SECONDS` | ❌ | `10` | Connection timeout |
| `BUILDIUM_READ_TIMEOUT_SECONDS` | ❌ | `30` | Read timeout |

**Note:** collections-sync `config.py` also defines timeouts (`BAL_TIMEOUT`, `LEASE_TIMEOUT`, `TENANT_TIMEOUT`) for cancellation.

#### GoogleSheetsConfig (prefix: `GOOGLE_SHEETS_`)

| Variable | Required | Default | Notes |
|----------|----------|---------|-------|
| `GOOGLE_SHEETS_CREDENTIALS_PATH` | ❌ | — | Path to service account JSON. If blank, uses Application Default Credentials. |

**Alternative:** Set `GOOGLE_APPLICATION_CREDENTIALS` env var (standard GCP practice).

---

## Data Models

### BuildiumClient Models (from core-integrations)

```python
# From core_integrations.buildium.models

@dataclass
class TenantDetails:
    """Tenant profile from GET /tenants/{tenantId}"""
    first_name: str
    last_name: str
    email: str | None
    phone_number: str | None  # Primary phone
    # ... more fields

@dataclass
class Lease:
    """Lease from GET /leases"""
    id: int
    property_id: int
    unit_id: int
    tenants: list[LeaseTenant]  # Tenants on the lease
    # ... more fields

@dataclass
class OutstandingBalance:
    """Balance from GET /accounting/outstanding-balances"""
    lease_id: int
    amount_owed: float
```

### GoogleSheetsClient Models (from core-integrations)

```python
# From core_integrations.google_sheets.models

@dataclass
class UpsertOptions:
    """Configuration for upsert_rows()"""
    sheet_title: str              # "Collections"
    header_row: int               # 1 (row number, not 0-indexed)
    data_row: int                 # 2 (first data row)
    key_header: str               # "Lease ID"
    ensure_headers: bool          # True (write headers if missing)
    headers: list[str]            # ["Lease ID", "Name", ...]
    num_columns: int              # 8 (or however many columns)
```

---

## Data Flow: Bulk Sync Example

Here's how collections-sync uses core-integrations for a bulk sync:

```
1. collections_sync/app.py:_run_bulk()
   └─► fetch_active_owed_rows(buildium_client, ...)
       from collections_sync/fetch.py

2. fetch.py orchestrates:
   ├─► buildium_client.fetch_outstanding_balances()
   │   (Get all {lease_id: balance} dict)
   │
   ├─► buildium_client.list_all_leases(max_pages=...)
   │   (Get all Lease objects with tenant lists)
   │
   └─► For each lease with a balance:
       └─► buildium_client.get_tenant_details(tenant_id)
           [3 concurrent workers with 250ms throttle]
           (Get TenantDetails: name, email, phone)

3. collections_sync/transform.py:to_sheet_values()
   └─► Convert fetched data to sheet row format

4. collections_sync/sheets_writer.py:CollectionsSheetsWriter.upsert_preserving()
   ├─► sheets_client.read_range() [read existing data]
   ├─► Compare new rows against existing rows
   ├─► sheets_client.upsert_rows() [merge + update]
   ├─► sheets_client.get_sheet_numeric_id()
   └─► sheets_client.apply_background_color() [yellow new rows]
```

---

## Error Handling & Retry Strategy

Both clients implement exponential backoff:

### BuildiumClient Retry Logic

```python
# From core_integrations/buildium/client.py
max_retries = 5
base_backoff_seconds = 2

# Retry on:
# - 429 (rate limit)
# - 5xx (server error)

# Backoff: 2s, 4s, 8s, 16s, 32s (with 50% jitter)
```

### GoogleSheetsClient Error Handling

- Validates credentials at init time (lazy service initialization)
- Raises `googleapiclient.errors.HttpError` on API failures
- Callers (collections-sync) catch and log these errors

### collections-sync Error Handling

```python
# From collections_sync/app.py
try:
    result = await _run_bulk(...)
except Exception as e:
    logger.exception("Sync failed")
    return HTTPException(status_code=500, detail=str(e))
```

Errors bubble up as 500 responses so the orchestrator (Cloud Scheduler) can retry.

---

## Key Differences: BuildiumClient is Sync, collections-sync is Async

**Problem:** BuildiumClient is synchronous (uses `httpx.Client`), but collections-sync is async (uses FastAPI).

**Solution:** Use `asyncio.to_thread()` to run sync BuildiumClient in a thread pool:

```python
# From collections_sync/fetch.py

async def fetch_active_owed_rows(...):
    loop = asyncio.get_event_loop()
    
    # Run sync client in thread pool
    balances = await loop.run_in_executor(
        None,
        buildium_client.fetch_outstanding_balances
    )
    
    leases = await loop.run_in_executor(
        None,
        lambda: buildium_client.list_all_leases(max_pages=max_pages)
    )
    
    # Concurrent tenant fetching with semaphore
    semaphore = asyncio.Semaphore(3)
    
    async def fetch_one_tenant(tenant_id):
        async with semaphore:
            return await loop.run_in_executor(
                None,
                buildium_client.get_tenant_details,
                tenant_id
            )
    
    tasks = [fetch_one_tenant(t) for t in tenant_ids]
    tenants = await asyncio.gather(*tasks)
```

**Benefits:**
- No modification needed to BuildiumClient (stays pure sync)
- Automatic thread pool management via `asyncio`
- Semaphore(3) limits concurrency to 3 workers
- 250ms throttle between requests

---

## Installation & Setup

### For Local Development

```bash
# Install core-integrations first (shared dependency)
cd /path/to/BRH/core-integrations
pip install -e .

# Install collections-sync
cd ../collections-sync-python
pip install -e .
```

### pyproject.toml Dependencies

collections-sync declares core-integrations as a dependency:

```toml
dependencies = [
    "core-integrations",  # ← This pulls in BuildiumClient, GoogleSheetsClient
    "fastapi>=0.111.0",
    "uvicorn[standard]>=0.29.0",
    "pydantic-settings>=2.3.0",
]
```

When you `pip install -e .` collections-sync, it also installs core-integrations (if not already installed).

---

## Testing Integration

### Mock Strategy

Unit tests in collections-sync mock both clients:

```python
# From tests/test_sheets_writer.py
from unittest.mock import MagicMock

mock_sheets_client = MagicMock()
mock_sheets_client.read_range.return_value = [
    ["Lease ID", "Name", "Amount Owed"],
    ["123", "John Doe", "5000.50"],
]

writer = CollectionsSheetsWriter(mock_sheets_client, ...)
result = writer.upsert_preserving([...])
```

### Smoke Test Against Real APIs

```bash
python smoke_test.py
```

This test:
1. Instantiates real BuildiumClient with credentials
2. Fetches real balances/leases from Buildium
3. Instantiates real GoogleSheetsClient
4. Reads from TEST_SHEET_ID (or SHEET_ID if TEST_SHEET_ID not set)
5. Validates data flow end-to-end

---

## Common Integration Issues

| Issue | Root Cause | Solution |
|-------|-----------|----------|
| `ImportError: No module named core_integrations` | core-integrations not installed | `pip install -e ../core-integrations` |
| `BuildiumClient returns None` | No BUILDIUM_KEY/BUILDIUM_SECRET | Check .env file |
| `GoogleSheetsClient.ping() returns False` | No GOOGLE_SHEETS_CREDENTIALS_PATH or GOOGLE_APPLICATION_CREDENTIALS | Check credentials env var |
| Rate limit errors (429) | Too many concurrent requests | Semaphore(3) is already in place; reduce throttle or increase timeout |
| "key header not found" | Sheet missing "Lease ID" column | Check WORKSHEET_NAME and column names |

---

## Summary

**core-integrations** provides:
- ✅ **BuildiumClient** — Reliable Buildium API wrapper with retry logic
- ✅ **GoogleSheetsClient** — Full-featured Sheets API client with upsert/formatting
- ✅ **UpsertOptions** — Configuration for smart row merging
- ✅ **Data models** — Strongly typed Pydantic models for all API responses

**collections-sync** orchestrates:
- Async concurrent tenant enrichment (3 workers, 250ms throttle)
- Column preservation (8 owned, 19 preserved)
- Intelligent row upsert with batch updates
- Yellow highlighting for new rows

The separation of concerns allows **core-integrations** to be shared across multiple projects (address_pipeline, etc.) while **collections-sync** focuses on the specific business logic of delinquent tenant tracking.
