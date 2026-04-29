# Collections Sync Data Flow Trace

## Overview

This document traces the complete flow of data through the collections-sync Python pipeline, from the initial HTTP request through Buildium API calls, concurrent enrichment, transformation, and finally into Google Sheets.

---

## 1. HTTP Request Entry Point

### File: `src/collections_sync/app.py` (lines 107-130)

```python
@app.post("/")
async def trigger_sync(request: SyncRequest) -> dict:
    """Trigger synchronization.

    Args:
        request: SyncRequest with mode, max_pages, max_rows.

    Returns:
        SyncResult as JSON.
    """
    cfg = app.state.cfg
    buildium = app.state.buildium
    sheets = app.state.sheets

    try:
        if request.mode == SyncMode.BULK:
            return await _run_bulk(cfg, buildium, sheets, request)
        else:
            return await _run_quick(cfg, buildium, sheets, request)
```

### Payload Structure: `SyncRequest`

**File:** `src/collections_sync/models.py` (lines 28-32)

```python
class SyncRequest(BaseModel):
    """HTTP request body for triggering sync."""
    mode: SyncMode = SyncMode.BULK
    max_pages: int = 0
    max_rows: int = 0
```

**Example Request:**
```bash
curl -X POST http://localhost:8080/ \
  -H "Content-Type: application/json" \
  -d '{"mode": "bulk", "max_pages": 0, "max_rows": 50}'
```

**Payload Breakdown:**
- `mode` — Sync strategy: "bulk" (full refresh) or "quick" (balance-only)
- `max_pages` — Pagination limit on Buildium lease fetch (0 = no cap)
- `max_rows` — Result set limit (0 = no cap)

---

## 2. Bulk Sync Orchestration

### File: `src/collections_sync/app.py` (lines 133-185)

```python
async def _run_bulk(
    cfg: CollectionsSyncConfig,
    buildium: BuildiumClient,
    sheets: GoogleSheetsClient,
    request: SyncRequest,
) -> dict:
    """Execute bulk sync mode."""
    logger.info("Starting bulk sync: max_pages=%d, max_rows=%d", 
                request.max_pages, request.max_rows)

    writer = CollectionsSheetsWriter(
        client=sheets,
        spreadsheet_id=cfg.effective_sheet_id,
        sheet_title=cfg.worksheet_name,
        header_row=cfg.header_row,
        data_row=cfg.data_row,
    )

    # Get existing lease IDs
    existing_keys, _ = writer.get_existing_key_rows()
    existing_lease_ids = {int(k) for k in existing_keys.keys() if k.isdigit()}

    logger.info("Found %d existing leases in sheet", len(existing_lease_ids))

    # Fetch and enrich rows
    rows, leases_scanned = await fetch_active_owed_rows(
        client=buildium,
        max_pages=request.max_pages,
        max_rows=request.max_rows,
        bal_timeout=float(cfg.bal_timeout),
        lease_timeout=float(cfg.lease_timeout),
        tenant_timeout=float(cfg.tenant_timeout),
        tenant_sleep_ms=cfg.tenant_sleep_ms,
        existing_lease_ids=existing_lease_ids,
    )

    # ... upsert to sheet ...
```

**Flow Control Decisions:**
1. Read existing lease IDs from Google Sheet (to avoid duplicate enrichment)
2. Pass these to `fetch_active_owed_rows()` to skip zero-balance non-existing leases

---

## 3. Buildium API Data Fetching

### File: `src/collections_sync/fetch.py` (lines 14-90)

```python
async def fetch_active_owed_rows(
    client: BuildiumClient,
    max_pages: int = 0,
    max_rows: int = 0,
    bal_timeout: float = 60.0,
    lease_timeout: float = 60.0,
    tenant_timeout: float = 60.0,
    tenant_sleep_ms: int = 250,
    existing_lease_ids: set[int] | None = None,
) -> tuple[list[DelinquentRow], int]:
    """Fetch and enrich delinquent rows from Buildium.

    Steps:
    1. Fetch all outstanding balances
    2. Fetch all leases
    3. Concurrently enrich with tenant details (3 workers max)
    4. Sort by amount owed descending
    """
```

### Step A: Fetch Outstanding Balances

**File:** `src/collections_sync/fetch.py` (lines 90-96)

```python
# Step A: Fetch outstanding balances
logger.info("Fetching outstanding balances...")
debt_map: dict[int, float] = await asyncio.to_thread(
    client.fetch_outstanding_balances
)
logger.info("Found %d leases with outstanding balances", len(debt_map))
```

**Buildium API Call Stack:**
```
collections_sync/fetch.py:fetch_active_owed_rows()
  └─► asyncio.to_thread(buildium_client.fetch_outstanding_balances)
      [Thread Pool Execution]
      
      └─► core_integrations/buildium/client.py:BuildiumClient.fetch_outstanding_balances()
          └─► GET /accounting/outstanding-balances?pageNumber=1&pageSize=1000
          └─► Paginate through all results
          └─► Return dict[lease_id: int, balance: float]
```

**Buildium Payload (Outgoing):**
- HTTP Method: `GET`
- Endpoint: `/v1/accounting/outstanding-balances`
- Auth: OAuth2 Bearer Token (from `BUILDIUM_KEY` + `BUILDIUM_SECRET`)
- Query Params: `pageNumber`, `pageSize`

**Buildium Response (Incoming):**
```json
{
  "pageNumber": 1,
  "pageSize": 1000,
  "totalPages": 3,
  "items": [
    {
      "leaseId": 12345,
      "balance": 5000.50
    },
    {
      "leaseId": 12346,
      "balance": 1200.00
    }
  ]
}
```

**Output to Next Stage:**
```python
debt_map = {
    12345: 5000.50,
    12346: 1200.00,
    # ... more leases ...
}
```

### Step B: Fetch All Leases

**File:** `src/collections_sync/fetch.py` (lines 97-102)

```python
# Step B: Fetch all leases
logger.info("Fetching leases...")
leases: list[Lease] = await asyncio.to_thread(
    lambda: client.list_all_leases(max_pages=max_pages)
)
logger.info("Fetched %d total leases", len(leases))
```

**Buildium API Call Stack:**
```
collections_sync/fetch.py:fetch_active_owed_rows()
  └─► asyncio.to_thread(lambda: buildium_client.list_all_leases(max_pages))
      [Thread Pool Execution]
      
      └─► core_integrations/buildium/client.py:BuildiumClient.list_all_leases(max_pages=0)
          └─► Loop: GET /leases?pageNumber=N&pageSize=1000
          └─► For each page, extract lease objects
          └─► Return list[Lease]
```

**Buildium Payload (Outgoing):**
- HTTP Method: `GET`
- Endpoint: `/v1/leases`
- Auth: OAuth2 Bearer Token
- Query Params: `pageNumber`, `pageSize`

**Buildium Response Structure (per lease):**
```json
{
  "id": 12345,
  "leaseFromDate": "2020-01-15",
  "leaseToDate": null,
  "unitNumber": "A1",
  "unit": {
    "id": 5678,
    "address": {
      "addressLine1": "123 Main St",
      "city": "Springfield",
      "state": "IL",
      "zipCode": "62701"
    }
  },
  "tenants": [
    {
      "id": 9999,
      "status": "Active"
    },
    {
      "id": 10000,
      "status": "Inactive"
    }
  ]
}
```

**Converted to Internal Model:**
```python
# core_integrations/buildium/models.py
Lease(
    id=12345,
    lease_to_date=None,
    unit_number="A1",
    tenants=[
        LeaseTenant(id=9999, status="Active"),
        LeaseTenant(id=10000, status="Inactive"),
    ],
    unit=Unit(
        id=5678,
        property_id=...,
        address=Address(address_line1="123 Main St", ...)
    )
)
```

**Output to Next Stage:**
```python
leases = [
    Lease(id=12345, tenants=[...], unit=...),
    Lease(id=12346, tenants=[...], unit=...),
    # ... 2098 more leases ...
]
```

### Step C: Concurrent Tenant Enrichment (The Complex Part)

**File:** `src/collections_sync/fetch.py` (lines 103-195)

#### Filtering & Task Creation

```python
# Launch tasks for relevant leases
tasks = []
for lease in leases:
    owed = debt_map.get(lease.id, 0.0)
    is_existing = lease.id in existing_lease_ids

    # Skip leases with no balance and no existing row
    if owed <= 0 and not is_existing:
        continue

    # Respect max_rows cap
    if max_rows > 0 and len(tasks) >= max_rows:
        break

    tasks.append(asyncio.create_task(enrich_lease(lease, owed)))
```

**Filter Logic:**
- Include lease if: `balance > 0` OR `lease_id in existing_lease_ids`
- Exclude if: `balance == 0` AND `not existing`

#### Concurrent Task Execution with Semaphore

```python
# Step C: Concurrent tenant enrichment with 3-worker semaphore
sem = asyncio.Semaphore(3)
tenant_cache: dict[int, TenantDetails] = {}
cache_lock = asyncio.Lock()
results: list[DelinquentRow] = []
results_lock = asyncio.Lock()

async def enrich_lease(lease: Lease, owed: float) -> None:
    """Enrich a lease with tenant details."""
    async with sem:  # ← Limits to 3 concurrent workers
        # ... pick tenant, check cache, fetch if needed ...
        
        await asyncio.sleep(tenant_sleep_ms / 1000.0)  # 250ms throttle
        
        td = await asyncio.to_thread(
            client.get_tenant_details, tenant_id
        )
        
        # ... build DelinquentRow and append to results ...
```

**Concurrency Strategy:**
```
Task Queue:              Worker Pool:
┌─────────────────┐     ┌──────────────────┐
│ Enrich Lease 1  │ ──→ │ Worker 1 (Busy)  │
│ Enrich Lease 2  │ ──→ │ Worker 2 (Busy)  │
│ Enrich Lease 3  │ ──→ │ Worker 3 (Busy)  │
│ Enrich Lease 4  │ ⏳  │ [Waiting...]     │ ← Semaphore blocks
│ Enrich Lease 5  │ ⏳  │                  │
│ ...             │     │                  │
└─────────────────┘     └──────────────────┘

When a worker finishes:
1. Worker 1 releases
2. Semaphore unblocks next task
3. Worker 1 picks up Lease 4
4. Repeat
```

**Tenant Lookup Buildium API Call:**

**File:** `src/collections_sync/fetch.py` (lines 138-159)

```python
# Fetch tenant details if not cached
td = await asyncio.to_thread(
    client.get_tenant_details, tenant_id
)
```

**Buildium API Call Stack:**
```
collections_sync/fetch.py:enrich_lease()
  └─► asyncio.to_thread(buildium_client.get_tenant_details, tenant_id)
      [Thread Pool Execution]
      
      └─► core_integrations/buildium/client.py:BuildiumClient.get_tenant_details(tenant_id)
          └─► GET /tenants/{tenant_id}
```

**Buildium Payload (Outgoing):**
- HTTP Method: `GET`
- Endpoint: `/v1/tenants/{tenant_id}`
- Auth: OAuth2 Bearer Token
- Path Param: `tenant_id` (e.g., 9999)

**Buildium Response:**
```json
{
  "id": 9999,
  "firstName": "John",
  "lastName": "Doe",
  "email": "john.doe@example.com",
  "phoneNumbers": [
    {
      "number": "555-1234",
      "type": "Mobile"
    }
  ],
  "address": {
    "addressLine1": "456 Tenant Ave",
    "city": "Springfield",
    "state": "IL",
    "zipCode": "62701"
  }
}
```

**Converted to Internal Model:**
```python
# core_integrations/buildium/models.py
TenantDetails(
    id=9999,
    first_name="John",
    last_name="Doe",
    email="john.doe@example.com",
    phone_numbers=[PhoneNumber(number="555-1234")],
    address=Address(address_line1="456 Tenant Ave", ...)
)
```

**Caching:**

```python
# Check cache first
async with cache_lock:
    cached = tenant_cache.get(tenant_id)

if cached is None:
    # Fetch and cache
    td = await asyncio.to_thread(client.get_tenant_details, tenant_id)
    async with cache_lock:
        tenant_cache[tenant_id] = td
else:
    td = cached
```

**DelinquentRow Creation:**

**File:** `src/collections_sync/fetch.py` (lines 170-186)

```python
async with results_lock:
    results.append(
        DelinquentRow(
            lease_id=lease.id,
            name=f"{td.first_name or ''} {td.last_name or ''}".strip(),
            address=addr,
            phone=_first_phone(td),
            email=td.email or "",
            amount_owed=owed,
            date_added=today,
        )
    )
```

**Payload at Results Lock:**
```python
DelinquentRow(
    lease_id=12345,
    name="John Doe",
    address="123 Main St",
    phone="555-1234",
    email="john.doe@example.com",
    amount_owed=5000.50,
    date_added="04/23/2026",
)
```

### Step D: Sorting

**File:** `src/collections_sync/fetch.py` (lines 196-206)

```python
# Sort by amount owed descending
results.sort(key=lambda r: r.amount_owed, reverse=True)

# Final trim in case tasks raced past max_rows
if max_rows > 0 and len(results) > max_rows:
    results = results[:max_rows]

return results, len(leases)
```

**Output to Next Stage:**
```python
[
    DelinquentRow(lease_id=12346, amount_owed=1500.00, ...),
    DelinquentRow(lease_id=12345, amount_owed=5000.50, ...),
    # ... sorted descending by amount_owed ...
]
```

---

## 4. Data Transformation

### File: `src/collections_sync/transform.py` (lines 36-91)

```python
def to_sheet_values(rows: list[DelinquentRow]) -> list[list[Any]]:
    """Convert a list of DelinquentRow to sheet row values.

    Each row is expanded to match the full HEADERS layout, with owned columns
    filled in and other columns left empty.
    """
    out = []
    now = datetime.now().strftime("%m/%d/%Y")

    for r in rows:
        row: list[Any] = [None] * len(HEADERS)  # 27 columns

        def set_value(header_name: str, value: Any) -> None:
            """Set a value at the column matching header_name."""
            normalized = header_name.strip().lower()
            idx = header_indices.get(normalized)
            if idx is not None and idx < len(row):
                row[idx] = value

        set_value("Date First Added", r.date_added)
        set_value("Name", r.name)
        set_value("Address:", r.address)
        set_value("Phone Number", r.phone)
        set_value("Email", r.email)
        set_value("Amount Owed:", r.amount_owed)
        set_value("Last Edited Date", now)
        set_value("Lease ID", r.lease_id)

        # Convert None to empty string for output
        row = [v if v is not None else "" for v in row]
        out.append(row)

    return out
```

**Column Definition:**

**File:** `src/collections_sync/transform.py` (lines 5-34)

```python
HEADERS: list[str] = [
    "Date First Added",        # [0]  ← OWNED
    "Name",                    # [1]  ← OWNED
    "Address:",                # [2]  ← OWNED
    "Phone Number",            # [3]  ← OWNED
    "Email",                   # [4]  ← OWNED
    "Amount Owed:",            # [5]  ← OWNED
    "Date of 5 Day:",          # [6]  (preserved)
    "Expired Lease",           # [7]  (preserved)
    "Returned Payment",        # [8]  (preserved)
    "Date of Next Payment",    # [9]  (preserved)
    "Date of Last payment",    # [10] (preserved)
    "Payment Plan Details",    # [11] (preserved)
    "Missed Payment Plan...",  # [12] (preserved)
    "Remarks:",                # [13] (preserved)
    "Last Edited Date",        # [14] ← OWNED
    "Status",                  # [15] (preserved)
    "CALL 1",                  # [16] (preserved)
    "CALL 2",                  # [17] (preserved)
    "CALL 3",                  # [18] (preserved)
    "CALL 4",                  # [19] (preserved)
    "CALL 5",                  # [20] (preserved)
    "Last Call Date",          # [21] (preserved)
    "Eviction Filed Date",     # [22] (preserved)
    "Eviction Court Date",     # [23] (preserved)
    "Lease ID",                # [24] ← OWNED (KEY)
    "Phone Number" (dup),      # [25] (preserved)
    "Date Status Changed...",  # [26] (preserved)
]

OWNED_HEADERS: set[str] = {
    "Date First Added",
    "Name",
    "Address:",
    "Phone Number",
    "Email",
    "Amount Owed:",
    "Lease ID",
    "Last Edited Date",
}
```

**Transformation Example:**

**Input DelinquentRow:**
```python
DelinquentRow(
    lease_id=12345,
    name="John Doe",
    address="123 Main St",
    phone="555-1234",
    email="john@test.com",
    amount_owed=5000.50,
    date_added="04/23/2026",
)
```

**Output (27-element array):**
```python
[
    "04/23/2026",              # [0]  Date First Added (OWNED)
    "John Doe",                # [1]  Name (OWNED)
    "123 Main St",             # [2]  Address (OWNED)
    "555-1234",                # [3]  Phone Number (OWNED)
    "john@test.com",           # [4]  Email (OWNED)
    5000.50,                   # [5]  Amount Owed (OWNED)
    "",                        # [6]  Date of 5 Day (preserved)
    "",                        # [7]  Expired Lease (preserved)
    "",                        # [8]  Returned Payment (preserved)
    "",                        # [9]  Date of Next Payment (preserved)
    "",                        # [10] Date of Last payment (preserved)
    "",                        # [11] Payment Plan Details (preserved)
    "",                        # [12] Missed Payment Plan (preserved)
    "",                        # [13] Remarks (preserved)
    "04/23/2026",              # [14] Last Edited Date (OWNED - TODAY)
    "",                        # [15] Status (preserved)
    "",                        # [16] CALL 1 (preserved)
    "",                        # [17] CALL 2 (preserved)
    "",                        # [18] CALL 3 (preserved)
    "",                        # [19] CALL 4 (preserved)
    "",                        # [20] CALL 5 (preserved)
    "",                        # [21] Last Call Date (preserved)
    "",                        # [22] Eviction Filed Date (preserved)
    "",                        # [23] Eviction Court Date (preserved)
    12345,                     # [24] Lease ID (OWNED - KEY)
    "",                        # [25] Phone Number dup (preserved)
    "",                        # [26] Date Status Changed (preserved)
]
```

**Output to Next Stage:**
```python
[
    [
        "04/23/2026", "John Doe", "123 Main St", "555-1234", "john@test.com",
        5000.50, "", "", "", "", "", "", "", "", "04/23/2026", "", "", "",
        "", "", "", "", "", "", 12345, "", ""
    ],
    [
        # ... more rows ...
    ]
]
```

---

## 5. Google Sheets Upsert

### File: `src/collections_sync/app.py` (lines 153-179)

```python
# Upsert to sheet
from .transform import HEADERS
rows_updated, rows_appended = writer.upsert_preserving(HEADERS, rows)

logger.info(
    "Upsert complete: %d updated, %d appended",
    rows_updated,
    rows_appended,
)

return asdict(SyncResult(
    mode=request.mode.value,
    existing_keys=len(existing_lease_ids),
    rows_prepared=len(rows),
    rows_updated=rows_updated,
    rows_appended=rows_appended,
    leases_scanned=leases_scanned,
))
```

### Upsert Implementation

**File:** `src/collections_sync/sheets_writer.py` (lines 109-282)

#### Read Existing Data

```python
def upsert_preserving(
    self,
    input_headers: list[str],
    new_rows: list[DelinquentRow],
) -> tuple[int, int]:
    """Upsert rows, preserving non-owned columns."""
    
    # Ensure sheet exists
    self.client.ensure_sheet(self.spreadsheet_id, self.sheet_title)

    # Read sheet headers
    sheet_headers, num_cols = self._read_sheet_headers()
    
    # Find key column
    key_idx = self._find_sheet_index(sheet_headers, self.key_header)
    
    # Read all existing data rows
    read_a1 = f"{self.sheet_title}!A{self.data_row}:{_col_letter(num_cols - 1)}50000"
    existing = self.client.read_range(self.spreadsheet_id, read_a1)
```

**Google Sheets API Calls (Outgoing):**

1. **Read Headers**
```python
read_a1 = "Collections!A1:ZZ1"
values = sheets_client.read_range(spreadsheet_id, read_a1)
```
API: `GET /v4/spreadsheets/{spreadsheet_id}/values/{range}`

2. **Read Existing Rows**
```python
read_a1 = "Collections!A2:Z50000"
existing = sheets_client.read_range(spreadsheet_id, read_a1)
```

**Google Sheets Response (Incoming):**
```json
{
  "range": "Collections!A2:Z100",
  "majorDimension": "ROWS",
  "values": [
    ["04/20/2026", "Jane Smith", "456 Oak Ave", "555-5678", "jane@test.com", 1200.00, "", "", "", "", "", "", "04/20/2026", "", "", "", "", "", "", "", "", "", "", "", 12346, "", ""],
    ["04/15/2026", "Bob Johnson", "789 Pine St", "555-9012", "bob@test.com", 3000.00, "", "", "", "", "", "", "04/15/2026", "", "", "", "", "", "", "", "", "", "", "", 12347, "", ""],
  ]
}
```

#### Merge Logic

**File:** `src/collections_sync/sheets_writer.py` (lines 188-242)

```python
# Build map of existing rows by key
existing_by_key: dict[str, list[Any]] = {}
for r in existing:
    k = _normalize_lease_id_key(str(norm_row[key_idx]))
    if k and k not in existing_by_key:
        existing_by_key[k] = norm_row

# Merge input rows with existing sheet rows
for input_row, sheet_row in zip(new_rows, sheet_values):
    k = _normalize_lease_id_key(str(sheet_row[key_idx]))
    
    # Check if this is an existing row
    if k in existing_by_key:
        # Copy existing row and selectively update owned columns
        out_row = list(existing_by_key[k])
    else:
        # New row
        out_row = [None] * num_cols
    
    # Merge owned column values
    for canonical, (in_idx, out_idx) in mapping.items():
        # Special case: preserve "Date First Added" for existing rows
        if (
            k in existing_by_key
            and canonical.strip().lower() == "date first added"
        ):
            existing_val = str(out_row[out_idx] or "").strip()
            if existing_val:
                continue  # Don't overwrite
        
        out_row[out_idx] = sheet_row[in_idx]
```

**Merge Example:**

**Existing Row in Sheet (Lease 12345):**
```python
[
    "04/20/2026",              # Date First Added (OLD - PRESERVE)
    "Jane Smith",              # Name (OLD)
    "456 Oak Ave",             # Address (OLD)
    "555-5678",                # Phone (OLD)
    "jane@test.com",           # Email (OLD)
    1200.00,                   # Amount Owed (OLD)
    "5 day sent",              # Date of 5 Day (MANUAL - PRESERVE)
    "No",                      # Expired Lease (MANUAL - PRESERVE)
    "",                        # ...
    "04/20/2026",              # Last Edited Date (OLD)
    "Payment arranged",        # Status (MANUAL - PRESERVE)
    "",                        # ...
    12345,                     # Lease ID (KEY)
]

**New Row from Buildium/Transform (Lease 12345):**
[
    "04/23/2026",              # Date First Added (NEW)
    "Jane Smith",              # Name (NEW - same)
    "456 Oak Ave",             # Address (NEW - same)
    "555-5678",                # Phone (NEW - same)
    "jane@test.com",           # Email (NEW - same)
    1500.00,                   # Amount Owed (NEW - UPDATED!)
    "",                        # Date of 5 Day (empty)
    "",                        # Expired Lease (empty)
    "",                        # ...
    "04/23/2026",              # Last Edited Date (NEW)
    "",                        # Status (empty)
    "",                        # ...
    12345,                     # Lease ID (KEY)
]

**Merged Result (Update):**
[
    "04/20/2026",              # Date First Added (PRESERVED - never overwrite!)
    "Jane Smith",              # Name (UPDATED)
    "456 Oak Ave",             # Address (UPDATED)
    "555-5678",                # Phone (UPDATED)
    "jane@test.com",           # Email (UPDATED)
    1500.00,                   # Amount Owed (UPDATED)
    "5 day sent",              # Date of 5 Day (PRESERVED - manual entry)
    "No",                      # Expired Lease (PRESERVED - manual entry)
    "",                        # ...
    "04/23/2026",              # Last Edited Date (UPDATED)
    "Payment arranged",        # Status (PRESERVED - manual entry!)
    "",                        # ...
    12345,                     # Lease ID (KEY)
]
```

#### Update vs Append Decision

**File:** `src/collections_sync/sheets_writer.py` (lines 242-273)

```python
# Split merged rows into updates and appends
update_ranges = []
to_append = []

for out_row in merged:
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
```

**Decision Tree:**
```
For each merged row:
  ├─ Lookup lease_id in key_to_row_num (existing keys from sheet read)
  ├─ If found:
  │   └─ Add to update_ranges
  │       Example: {"range": "Collections!A2:Z2", "values": [[...]]}
  └─ If NOT found:
      └─ Add to to_append
          Example: [["04/23/2026", "John Doe", ...]]
```

#### Batch Update API Calls

**File:** `src/collections_sync/sheets_writer.py` (lines 274-290)

```python
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
    start_row = max(key_to_row_num.values()) + 1
    end_row = start_row + len(to_append) - 1
    append_a1 = f"{self.sheet_title}!{_col_letter(0)}{start_row}:{_col_letter(num_cols - 1)}{end_row}"

    self.client.write_range(self.spreadsheet_id, append_a1, to_append)
    rows_appended = len(to_append)
```

**Google Sheets API Calls (Outgoing):**

**1. Batch Updates:**
```python
updates = [
    {
        "range": "Collections!A2:Z2",
        "values": [["04/20/2026", "Jane Smith", "456 Oak Ave", ..., 1500.00, ..., 12345]]
    },
    {
        "range": "Collections!A3:Z3",
        "values": [["04/15/2026", "Bob Johnson", "789 Pine St", ..., 3000.00, ..., 12347]]
    },
]

client.batch_update_values(spreadsheet_id, updates, chunk_size=200, pause_ms=150)
```

**API Method:** `PUT /v4/spreadsheets/{spreadsheet_id}/values:batchUpdate`

**Request Body:**
```json
{
  "data": [
    {
      "range": "Collections!A2:Z2",
      "values": [["04/20/2026", "Jane Smith", ...]]
    },
    {
      "range": "Collections!A3:Z3",
      "values": [["04/15/2026", "Bob Johnson", ...]]
    }
  ],
  "valueInputOption": "USER_ENTERED"
}
```

**2. Append (Write New Rows):**
```python
start_row = 102  # After existing rows
append_a1 = "Collections!A102:Z150"
to_append = [
    ["04/23/2026", "John Doe", "123 Main St", ..., 5000.50, ..., 12345],
    ["04/23/2026", "Alice Brown", "321 Elm St", ..., 2500.00, ..., 12348],
]

client.write_range(spreadsheet_id, append_a1, to_append)
```

**API Method:** `PUT /v4/spreadsheets/{spreadsheet_id}/values/{range}`

**Request Body:**
```json
{
  "range": "Collections!A102:Z150",
  "values": [
    ["04/23/2026", "John Doe", "123 Main St", ..., 5000.50, ..., 12345],
    ["04/23/2026", "Alice Brown", "321 Elm St", ..., 2500.00, ..., 12348]
  ],
  "majorDimension": "ROWS"
}
```

#### Apply Yellow Background to New Rows

**File:** `src/collections_sync/sheets_writer.py` (lines 299-314)

```python
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
```

**Google Sheets API Calls (Outgoing):**

**1. Get Sheet Numeric ID:**
```python
numeric_id = client.get_sheet_numeric_id(spreadsheet_id, "Collections")
```

**API Method:** `GET /v4/spreadsheets/{spreadsheet_id}`

**Response:**
```json
{
  "spreadsheetId": "...",
  "properties": {...},
  "sheets": [
    {
      "properties": {
        "sheetId": 0,
        "title": "Collections",
        ...
      }
    }
  ]
}
```

**2. Apply Background Color:**
```python
client.apply_background_color(
    spreadsheet_id,
    sheet_id=0,
    start_row_0indexed=101,  # Rows 102-150
    end_row_exclusive=151,
    num_cols=27,
    red=1.0,
    green=0.98,
    blue=0.8,
)
```

**API Method:** `POST /v4/spreadsheets/{spreadsheet_id}:batchUpdate`

**Request Body:**
```json
{
  "requests": [
    {
      "updateCells": {
        "range": {
          "sheetId": 0,
          "startRowIndex": 101,
          "endRowIndex": 151,
          "startColumnIndex": 0,
          "endColumnIndex": 27
        },
        "rows": [
          {
            "values": [
              {
                "userEnteredFormat": {
                  "backgroundColor": {
                    "red": 1.0,
                    "green": 0.98,
                    "blue": 0.8
                  }
                }
              },
              ...
            ]
          },
          ...
        ],
        "fields": "userEnteredFormat.backgroundColor"
      }
    }
  ]
}
```

---

## 6. Response & Completion

### File: `src/collections_sync/app.py` (lines 176-185)

```python
return asdict(SyncResult(
    mode=request.mode.value,
    existing_keys=len(existing_lease_ids),
    rows_prepared=len(rows),
    rows_updated=rows_updated,
    rows_appended=rows_appended,
    leases_scanned=leases_scanned,
))
```

**HTTP Response (Outgoing):**
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

**Status Code:** 200 OK

---

## Complete Data Flow Diagram

```
┌─────────────────────────────────────────────────────────────────────────┐
│ HTTP Client                                                             │
│ POST / with {"mode": "bulk", "max_pages": 0, "max_rows": 50}          │
└─────────────────────────────────────────────────────────────────────────┘
                                    ↓
┌─────────────────────────────────────────────────────────────────────────┐
│ collections_sync/app.py:trigger_sync()                                 │
│ - Parse SyncRequest(mode, max_pages, max_rows)                          │
│ - Route to _run_bulk()                                                  │
└─────────────────────────────────────────────────────────────────────────┘
                                    ↓
┌─────────────────────────────────────────────────────────────────────────┐
│ collections_sync/app.py:_run_bulk()                                    │
│ - Read existing sheet keys: {12345, 12346, 12347, ...}                 │
│ - Call fetch_active_owed_rows(buildium_client, ...)                    │
└─────────────────────────────────────────────────────────────────────────┘
                                    ↓
         ┌──────────────────────────┴──────────────────────────┐
         ↓                                                       ↓
┌─────────────────────────┐                  ┌─────────────────────────┐
│ Buildium API: Step A    │                  │ Buildium API: Step B    │
│ fetch_outstanding_      │                  │ list_all_leases()       │
│ balances()              │                  │                         │
│                         │                  │ Returns:                │
│ GET /accounting/        │                  │ list[Lease]             │
│ outstanding-balances    │                  │ 2100 total leases       │
│                         │                  │                         │
│ Returns:                │                  │                         │
│ {                       │                  │                         │
│   12345: 5000.50,       │                  │                         │
│   12346: 1200.00,       │                  │                         │
│   ...                   │                  │                         │
│ }                       │                  │                         │
│ Paginated (1000/page)   │                  │                         │
└─────────────────────────┘                  └─────────────────────────┘
         ↓                                                       ↓
         └──────────────────────────┬──────────────────────────┘
                                    ↓
┌─────────────────────────────────────────────────────────────────────────┐
│ collections_sync/fetch.py:fetch_active_owed_rows()                     │
│                                                                          │
│ Filter & Create Tasks:                                                 │
│ - Skip: balance=0 AND not existing                                      │
│ - Include: balance>0 OR existing                                        │
│ - Create up to max_rows tasks                                           │
│ - Result: 37 tasks to enrich                                            │
└─────────────────────────────────────────────────────────────────────────┘
                                    ↓
┌─────────────────────────────────────────────────────────────────────────┐
│ collections_sync/fetch.py:enrich_lease() [Concurrent x3]               │
│                                                                          │
│ Semaphore(3) limits concurrency:                                       │
│ ┌──────────────┐  ┌──────────────┐  ┌──────────────┐                  │
│ │ Worker 1     │  │ Worker 2     │  │ Worker 3     │                  │
│ │              │  │              │  │              │                  │
│ │ Task A:      │  │ Task B:      │  │ Task C:      │                  │
│ │ Pick tenant  │  │ Pick tenant  │  │ Pick tenant  │                  │
│ │ Check cache  │  │ Check cache  │  │ Check cache  │                  │
│ │ Sleep 250ms  │  │ Sleep 250ms  │  │ Sleep 250ms  │                  │
│ │              │  │              │  │              │                  │
│ │ GET /tenants │  │ GET /tenants │  │ GET /tenants │                  │
│ │ /{id}        │  │ /{id}        │  │ /{id}        │                  │
│ │              │  │              │  │              │                  │
│ │ Build        │  │ Build        │  │ Build        │                  │
│ │ DelinquentRow│  │ DelinquentRow│  │ DelinquentRow│                  │
│ │              │  │              │  │              │                  │
│ │ Append to    │  │ Append to    │  │ Append to    │                  │
│ │ results[]    │  │ results[]    │  │ results[]    │                  │
│ └──────────────┘  └──────────────┘  └──────────────┘                  │
│                                                                          │
│ Queue (awaiting workers):                                              │
│ ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ...            │
│ │ Task D       │  │ Task E       │  │ Task F       │                  │
│ └──────────────┘  └──────────────┘  └──────────────┘                  │
│                                                                          │
│ Results:                                                               │
│ [                                                                       │
│   DelinquentRow(lease_id=12345, amount_owed=5000.50, ...),            │
│   DelinquentRow(lease_id=12346, amount_owed=1200.00, ...),            │
│   ...                                                                   │
│ ]                                                                       │
│                                                                          │
│ Sort by amount_owed descending                                         │
│ Final trim to max_rows                                                 │
│                                                                          │
│ Return: (list[DelinquentRow], leases_scanned=2100)                    │
└─────────────────────────────────────────────────────────────────────────┘
                                    ↓
┌─────────────────────────────────────────────────────────────────────────┐
│ collections_sync/transform.py:to_sheet_values()                        │
│                                                                          │
│ Expand 37 DelinquentRow to 37 x 27-column arrays                       │
│                                                                          │
│ For each DelinquentRow:                                                │
│ - Set columns 0-7 (OWNED): date_added, name, address, phone, email,   │
│   amount_owed, lease_id, last_edited_date                             │
│ - Leave columns 8-26 (PRESERVED) empty ("")                            │
│                                                                          │
│ Output: list[list[Any]] — 37 rows x 27 columns                        │
└─────────────────────────────────────────────────────────────────────────┘
                                    ↓
┌─────────────────────────────────────────────────────────────────────────┐
│ collections_sync/sheets_writer.py:upsert_preserving()                  │
│                                                                          │
│ 1. Read sheet headers (API: GET /values/...)                           │
│ 2. Read existing rows (API: GET /values/...)                           │
│ 3. Build map of existing rows by key (lease_id)                        │
│ 4. Merge logic:                                                         │
│    - For each new row:                                                 │
│      ├─ If lease_id exists: MERGE (update owned, preserve manual)     │
│      └─ If lease_id new: APPEND                                        │
│ 5. Separate into update_ranges and to_append                           │
│ 6. Call batch_update_values() for 20 updates                           │
│    (API: PUT /values:batchUpdate)                                      │
│ 7. Call write_range() for 17 new rows                                  │
│    (API: PUT /values/{range})                                          │
│ 8. Apply yellow background to new rows                                 │
│    (API: GET /spreadsheets/ then POST /:batchUpdate)                   │
│                                                                          │
│ Return: (rows_updated=20, rows_appended=17)                            │
└─────────────────────────────────────────────────────────────────────────┘
                                    ↓
┌─────────────────────────────────────────────────────────────────────────┐
│ collections_sync/app.py:_run_bulk() [continued]                       │
│                                                                          │
│ Return SyncResult:                                                      │
│ {                                                                       │
│   "mode": "bulk",                                                       │
│   "existing_keys": 42,                                                  │
│   "rows_prepared": 37,                                                  │
│   "rows_updated": 20,                                                   │
│   "rows_appended": 17,                                                  │
│   "leases_scanned": 2100                                                │
│ }                                                                       │
└─────────────────────────────────────────────────────────────────────────┘
                                    ↓
┌─────────────────────────────────────────────────────────────────────────┐
│ HTTP Response (200 OK)                                                  │
│                                                                          │
│ {                                                                       │
│   "mode": "bulk",                                                       │
│   "existing_keys": 42,                                                  │
│   "rows_prepared": 37,                                                  │
│   "rows_updated": 20,                                                  │
│   "rows_appended": 17,                                                  │
│   "leases_scanned": 2100                                                │
│ }                                                                       │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## Quick Sync Path (Alternative)

### File: `src/collections_sync/app.py` (lines 187-237)

```python
async def _run_quick(
    cfg: CollectionsSyncConfig,
    buildium: BuildiumClient,
    sheets: GoogleSheetsClient,
    request: SyncRequest,
) -> dict:
    """Execute quick sync mode (balance-only updates)."""
    logger.info("Starting quick sync")

    writer = CollectionsSheetsWriter(
        client=sheets,
        spreadsheet_id=cfg.effective_sheet_id,
        sheet_title=cfg.worksheet_name,
        header_row=cfg.header_row,
        data_row=cfg.data_row,
    )

    # Get existing lease IDs
    key_to_row, sheet_headers = writer.get_existing_key_rows()

    if not key_to_row:
        logger.info("No existing leases in sheet")
        return asdict(SyncResult(mode=request.mode.value, existing_keys=0))

    # Fetch only balances for these leases
    existing_lease_ids = [int(k) for k in key_to_row.keys() if k.isdigit()]
    balances = await _fetch_balances(buildium, existing_lease_ids)

    # Update balances
    rows_updated = writer.quick_update_balances(key_to_row, sheet_headers, balances)

    logger.info("Quick sync complete: %d rows updated", rows_updated)

    return asdict(SyncResult(
        mode=request.mode.value,
        existing_keys=len(key_to_row),
        rows_updated=rows_updated,
    ))


async def _fetch_balances(client: BuildiumClient, lease_ids: list[int]) -> dict[int, float]:
    """Fetch balances for specific leases."""
    def _do_fetch():
        return client.fetch_outstanding_balances_for_lease_ids(lease_ids)

    import asyncio
    return await asyncio.to_thread(_do_fetch)
```

**Quick Sync Data Flow:**

```
HTTP Request {"mode": "quick", ...}
    ↓
Read existing lease IDs from sheet
{12345, 12346, 12347, ...}  [42 leases]
    ↓
Buildium API:
fetch_outstanding_balances_for_lease_ids([12345, 12346, ...])
GET /accounting/outstanding-balances (chunked by lease ID)
    ↓
Returns: {12345: 5000.50, 12346: 1200.00, ...}
    ↓
collections_sync/sheets_writer.py:quick_update_balances()
- Find "Amount Owed:" column index
- Find "Last Edited Date" column index
- For each lease_id->row mapping:
  ├─ Add update: {range: "Collections!F2", values: [[5000.50]]}
  └─ Add update: {range: "Collections!O2", values: [["04/23/2026"]]}
    ↓
Batch update via API: PUT /values:batchUpdate
    ↓
Response: {"mode": "quick", "existing_keys": 42, "rows_updated": 42}
```

---

## Key Payload Transformation Points

### 1. **Buildium → DelinquentRow**
- **Input:** Buildium Lease + TenantDetails + balance float
- **Process:** Extract name, phone, email from tenant; use lease address fallback
- **Output:** DelinquentRow dataclass

### 2. **DelinquentRow → Sheet Array (27 columns)**
- **Input:** DelinquentRow
- **Process:** Expand to full HEADERS width, empty unowned columns
- **Output:** list[Any] (27 elements)

### 3. **Sheet Array + Existing Row → Merged Row**
- **Input:** New array + existing row from sheet
- **Process:** Copy existing row, overwrite owned columns only, preserve manual entries
- **Output:** Merged row (preserves "Date First Added", "Status", notes, etc.)

### 4. **Merged Rows → API Batches**
- **Input:** list[Merged Row]
- **Process:** Split into updates vs appends, batch in 200-row chunks
- **Output:** batchUpdate + write API requests

---

## Summary Table

| Stage | Input | Output | APIs |
|-------|-------|--------|------|
| HTTP Entry | SyncRequest (JSON) | SyncRequest object | None |
| Orchestration | SyncRequest + Config | Lease IDs to enrich | Google Sheets: read_range |
| Balance Fetch | max_pages param | dict[lease_id: balance] | Buildium: GET /outstanding-balances |
| Lease Fetch | max_pages param | list[Lease] | Buildium: GET /leases |
| Tenant Enrichment | Lease + balance | DelinquentRow | Buildium: GET /tenants/{id} (3 workers) |
| Sorting | list[DelinquentRow] | Sorted by amount_owed | None |
| Transform | DelinquentRow | 27-column arrays | None |
| Merge | New arrays + existing | Merged arrays | Google Sheets: read_range |
| Upsert | Merged arrays | Updates + appends | Google Sheets: batch_update + write |
| Format | new row range | Yellow background | Google Sheets: batchUpdate |
| Response | Results metadata | SyncResult JSON | None |

---

## Configuration Variables Affecting Payload

**File:** `src/collections_sync/config.py`

| Variable | Used For | Default | Impact |
|----------|----------|---------|--------|
| `SHEET_ID` | Google Sheets spreadsheet ID | (required) | Target for all API calls |
| `WORKSHEET_NAME` | Google Sheets tab name | (required) | A1 notation building |
| `BUILDIUM_KEY` | Buildium OAuth2 client ID | (required) | Authorization header |
| `BUILDIUM_SECRET` | Buildium OAuth2 secret | (required) | Authorization header |
| `HEADER_ROW` | Google Sheets header row number | 1 | Range calculation |
| `DATA_ROW` | Google Sheets first data row | 2 | Range calculation |
| `TENANT_SLEEP_MS` | Throttle between tenant API calls | 250 | Concurrency timing |
| `TENANT_TIMEOUT` | Timeout per tenant fetch | 60 | Abort criterion |
| `BAL_TIMEOUT` | Balance fetch timeout | 60 | Abort criterion |
| `LEASE_TIMEOUT` | Lease fetch timeout | 60 | Abort criterion |

