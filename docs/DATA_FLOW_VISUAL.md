# Collections Sync — Payload Transformation Quick Reference

## Visual Payload Journey

### Stage 1: HTTP Request
```
┌─────────────────────────────────────────────────────────────┐
│ HTTP Client                                                 │
│                                                             │
│ POST http://localhost:8080/                                │
│ Content-Type: application/json                             │
│                                                             │
│ {                                                           │
│   "mode": "bulk",                                           │
│   "max_pages": 0,                                           │
│   "max_rows": 50                                            │
│ }                                                           │
└─────────────────────────────────────────────────────────────┘
                            ↓
                   [Pydantic Validation]
                            ↓
┌─────────────────────────────────────────────────────────────┐
│ Python Object: SyncRequest                                  │
│                                                             │
│ SyncRequest(                                                │
│   mode=SyncMode.BULK,        # Enum: "bulk"               │
│   max_pages=0,               # int                         │
│   max_rows=50                # int                         │
│ )                                                           │
└─────────────────────────────────────────────────────────────┘
File: src/collections_sync/models.py (lines 28-32)
```

---

### Stage 2: Buildium Outstanding Balances
```
┌─────────────────────────────────────────────────────────────┐
│ Buildium API Response                                       │
│                                                             │
│ GET /v1/accounting/outstanding-balances?pageNumber=1       │
│                                                             │
│ {                                                           │
│   "pageNumber": 1,                                          │
│   "pageSize": 1000,                                         │
│   "totalPages": 3,                                          │
│   "items": [                                                │
│     {                                                       │
│       "leaseId": 12345,                                     │
│       "balance": 5000.50                                    │
│     },                                                      │
│     {                                                       │
│       "leaseId": 12346,                                     │
│       "balance": 1200.00                                    │
│     },                                                      │
│     ... [998 more items] ...                               │
│   ]                                                         │
│ }                                                           │
│                                                             │
│ [PAGINATION: repeat for pages 2, 3, ...]                   │
└─────────────────────────────────────────────────────────────┘
                            ↓
                  [Parse & Flatten]
                            ↓
┌─────────────────────────────────────────────────────────────┐
│ Python Dict: Outstanding Balances                           │
│                                                             │
│ debt_map = {                                                │
│   12345: 5000.50,                                           │
│   12346: 1200.00,                                           │
│   12347: 0.00,        # ← might be filtered out later      │
│   12348: 3000.75,                                           │
│   ... [2096 more] ...                                       │
│ }                                                           │
│                                                             │
│ len(debt_map) = 2100                                        │
└─────────────────────────────────────────────────────────────┘
File: src/collections_sync/fetch.py (lines 90-96)
```

---

### Stage 3: Buildium Lease List
```
┌─────────────────────────────────────────────────────────────┐
│ Buildium API Response (Per Lease)                           │
│                                                             │
│ GET /v1/leases?pageNumber=1&pageSize=1000                  │
│                                                             │
│ {                                                           │
│   "id": 12345,                                              │
│   "leaseFromDate": "2020-01-15",                            │
│   "leaseToDate": null,                                      │
│   "unitNumber": "A1",                                       │
│   "unit": {                                                 │
│     "id": 5678,                                             │
│     "propertyId": 9999,                                     │
│     "address": {                                            │
│       "addressLine1": "123 Main St",                        │
│       "city": "Springfield",                                │
│       "state": "IL",                                        │
│       "zipCode": "62701"                                    │
│     }                                                       │
│   },                                                        │
│   "tenants": [                                              │
│     {                                                       │
│       "id": 9999,                                           │
│       "firstName": "John",                                  │
│       "lastName": "Doe",                                    │
│       "status": "Active"                                    │
│     },                                                      │
│     {                                                       │
│       "id": 10000,                                          │
│       "firstName": "Jane",                                  │
│       "lastName": "Smith",                                  │
│       "status": "Inactive"                                  │
│     }                                                       │
│   ]                                                         │
│ }                                                           │
└─────────────────────────────────────────────────────────────┘
                            ↓
                  [Parse & Convert]
                            ↓
┌─────────────────────────────────────────────────────────────┐
│ Python Dataclass: Lease                                     │
│                                                             │
│ Lease(                                                      │
│   id=12345,                                                 │
│   lease_to_date=None,                                       │
│   unit_number="A1",                                         │
│   unit=Unit(                                                │
│     id=5678,                                                │
│     property_id=9999,                                       │
│     address=Address(                                        │
│       address_line1="123 Main St",                          │
│       city="Springfield",                                   │
│       state="IL",                                           │
│       zip_code="62701"                                      │
│     )                                                       │
│   ),                                                        │
│   tenants=[                                                 │
│     LeaseTenant(id=9999, status="Active"),                 │
│     LeaseTenant(id=10000, status="Inactive")               │
│   ],                                                        │
│   current_tenants=[...]                                     │
│ )                                                           │
│                                                             │
│ [COMPLETE LIST]                                             │
│ leases = [Lease(...), Lease(...), ... Lease(...)]          │
│ len(leases) = 2100                                          │
└─────────────────────────────────────────────────────────────┘
File: src/collections_sync/fetch.py (lines 97-102)
```

---

### Stage 4: Filter by Balance & Existing
```
┌─────────────────────────────────────────────────────────────┐
│ Inputs:                                                     │
│   • leases: [Lease, Lease, ..., Lease]  (2100 total)       │
│   • debt_map: {lease_id: balance, ...}                      │
│   • existing_lease_ids: {12345, 12347}                      │
│   • max_rows: 50                                            │
└─────────────────────────────────────────────────────────────┘
                            ↓
                    [Filter Logic]
        For each lease:
        • Skip if: balance ≤ 0 AND not in existing
        • Include if: balance > 0 OR in existing
        • Stop if: task count ≥ max_rows
                            ↓
┌─────────────────────────────────────────────────────────────┐
│ Python List: Filtered Tasks                                 │
│                                                             │
│ tasks = [                                                   │
│   asyncio.Task(enrich_lease(Lease(12345), 5000.50)),       │
│   asyncio.Task(enrich_lease(Lease(12346), 1200.00)),       │
│   asyncio.Task(enrich_lease(Lease(12347), 0.00)),    ← exists
│   asyncio.Task(enrich_lease(Lease(12348), 3000.75)),       │
│   asyncio.Task(enrich_lease(Lease(12349), 2500.00)),       │
│   ... [32 more tasks to reach max_rows=50] ...             │
│ ]                                                           │
│                                                             │
│ len(tasks) = 37 (max_rows=50, but fewer delinquent)        │
└─────────────────────────────────────────────────────────────┘
File: src/collections_sync/fetch.py (lines 206-226)
```

---

### Stage 5: Concurrent Tenant Enrichment (THE KEY STAGE)
```
┌──────────────────────────────────────────────────────────────┐
│ Semaphore(3): Controls Concurrency                          │
│                                                              │
│ ┌──────────────┐  ┌──────────────┐  ┌──────────────┐        │
│ │ Worker 1     │  │ Worker 2     │  │ Worker 3     │        │
│ │              │  │              │  │              │        │
│ │ Task A:      │  │ Task B:      │  │ Task C:      │        │
│ │              │  │              │  │              │        │
│ │ • Pick       │  │ • Pick       │  │ • Pick       │        │
│ │   Active     │  │   Active     │  │   Active     │        │
│ │   Tenant     │  │   Tenant     │  │   Tenant     │        │
│ │              │  │              │  │              │        │
│ │ • Check      │  │ • Check      │  │ • Check      │        │
│ │   Cache      │  │   Cache      │  │   Cache      │        │
│ │              │  │              │  │              │        │
│ │ • Sleep      │  │ • Sleep      │  │ • Sleep      │        │
│ │   250ms      │  │   250ms      │  │   250ms      │        │
│ │              │  │              │  │              │        │
│ │ • Buildium   │  │ • Buildium   │  │ • Buildium   │        │
│ │   GET        │  │   GET        │  │   GET        │        │
│ │   /tenants   │  │   /tenants   │  │   /tenants   │        │
│ │   /9999      │  │   /10001     │  │   /10003     │        │
│ │              │  │              │  │              │        │
│ │ • Build      │  │ • Build      │  │ • Build      │        │
│ │   Row A      │  │   Row B      │  │   Row C      │        │
│ │              │  │              │  │              │        │
│ │ • Append to  │  │ • Append to  │  │ • Append to  │        │
│ │   results[]  │  │   results[]  │  │   results[]  │        │
│ │              │  │              │  │              │        │
│ │ [RELEASE]    │  │ [RELEASE]    │  │ [RELEASE]    │        │
│ └──────────────┘  └──────────────┘  └──────────────┘        │
│                                                              │
│ Queue (waiting for worker):                                │
│ ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ...  │
│ │ Task D       │  │ Task E       │  │ Task F       │       │
│ └──────────────┘  └──────────────┘  └──────────────┘       │
│                                                              │
│ Timeline:                                                  │
│ T0:    Workers 1,2,3 start (sem permits 3)                │
│ T250ms: Worker 1 gets response, appends Row A              │
│ T250ms: Semaphore releases, Task D starts                 │
│ T500ms: Worker 2 gets response                             │
│ T500ms: Semaphore releases, Task E starts                 │
│ ...                                                        │
│ T10s:  All 37 tasks complete                              │
└──────────────────────────────────────────────────────────────┘
File: src/collections_sync/fetch.py (lines 103-195)
```

#### Buildium Tenant Detail Request (Repeated 37 times concurrently)
```
┌──────────────────────────────────────────────────────────────┐
│ For each tenant_id in the 37 leases:                        │
│                                                              │
│ GET /v1/tenants/{tenant_id}                                 │
│ Authorization: Bearer {BUILDIUM_KEY:BUILDIUM_SECRET}         │
│                                                              │
│ Response:                                                    │
│ {                                                            │
│   "id": 9999,                                                │
│   "firstName": "John",                                       │
│   "lastName": "Doe",                                         │
│   "email": "john.doe@example.com",                           │
│   "phoneNumbers": [                                          │
│     {                                                        │
│       "number": "555-1234",                                  │
│       "type": "Mobile"                                       │
│     }                                                        │
│   ],                                                         │
│   "address": {                                               │
│     "addressLine1": "456 Tenant Ave",                        │
│     "city": "Springfield",                                   │
│     "state": "IL",                                           │
│     "zipCode": "62701"                                       │
│   }                                                          │
│ }                                                            │
│                                                              │
│ [CACHED in memory after first fetch]                        │
└──────────────────────────────────────────────────────────────┘
                            ↓
                  [Parse & Cache]
                            ↓
┌──────────────────────────────────────────────────────────────┐
│ TenantDetails object in cache:                              │
│                                                              │
│ tenant_cache = {                                             │
│   9999: TenantDetails(                                       │
│     id=9999,                                                 │
│     first_name="John",                                       │
│     last_name="Doe",                                         │
│     email="john.doe@example.com",                            │
│     phone_numbers=[PhoneNumber(number="555-1234")],          │
│     address=Address(address_line1="456 Tenant Ave", ...)     │
│   ),                                                         │
│   10001: TenantDetails(...),                                 │
│   10003: TenantDetails(...),                                 │
│   ...                                                        │
│ }                                                            │
│                                                              │
│ Size: ~20-30 entries (multiple leases may share tenant)      │
└──────────────────────────────────────────────────────────────┘
```

#### DelinquentRow Creation (per lease)
```
┌──────────────────────────────────────────────────────────────┐
│ Merge data sources for a single lease:                      │
│                                                              │
│ Lease (from Step 3):                                        │
│   id=12345                                                   │
│   unit.address="123 Main St"                                │
│   tenants=[LeaseTenant(id=9999, status="Active"), ...]      │
│                                                              │
│ Balance (from Step 2):                                      │
│   12345 → 5000.50                                           │
│                                                              │
│ TenantDetails (from cache/API):                             │
│   id=9999                                                    │
│   first_name="John"                                         │
│   last_name="Doe"                                           │
│   phone_numbers=[PhoneNumber(number="555-1234")]            │
│   email="john.doe@example.com"                              │
│   address="456 Tenant Ave"  (fallback if no unit addr)      │
│                                                              │
│ Current Date:                                               │
│   today="04/23/2026"  (MM/DD/YYYY format)                  │
│                                                              │
│ ┌────────────────────────────────────────────────────────┐  │
│ │ Address Selection Logic:                               │  │
│ │  if lease.unit.address:                                │  │
│ │    use lease.unit.address  ← PREFER THIS               │  │
│ │  elif tenant.address:                                  │  │
│ │    use tenant.address  ← fallback                       │  │
│ │  else:                                                 │  │
│ │    use ""  ← not found                                 │  │
│ └────────────────────────────────────────────────────────┘  │
│                                                              │
│ Phone Selection Logic:                                      │
│  if tenant.phone_numbers:                                   │
│    use tenant.phone_numbers[0].number  ← FIRST PHONE        │
│  else:                                                      │
│    use ""  ← not found                                     │  │
└──────────────────────────────────────────────────────────────┘
                            ↓
                   [Construct Object]
                            ↓
┌──────────────────────────────────────────────────────────────┐
│ DelinquentRow (Output of enrichment):                       │
│                                                              │
│ DelinquentRow(                                               │
│   lease_id=12345,              # ← from Lease.id            │
│   name="John Doe",             # ← f"{tenant.first_name}    │
│                                #    {tenant.last_name}"     │
│   address="123 Main St",       # ← from unit OR tenant       │
│   phone="555-1234",            # ← from tenant[0] OR ""      │
│   email="john.doe@example.com",# ← from tenant OR ""         │
│   amount_owed=5000.50,         # ← from debt_map[lease_id]   │
│   date_added="04/23/2026",     # ← today                     │
│ )                                                            │
│                                                              │
│ [APPEND TO results[] UNDER LOCK]                            │
└──────────────────────────────────────────────────────────────┘
File: src/collections_sync/fetch.py (lines 103-186)
```

---

### Stage 6: Sort & Finalize
```
┌──────────────────────────────────────────────────────────────┐
│ Before Sort:                                                │
│                                                              │
│ results = [                                                  │
│   DelinquentRow(lease_id=12345, amount_owed=5000.50),       │
│   DelinquentRow(lease_id=12346, amount_owed=1200.00),       │
│   DelinquentRow(lease_id=12348, amount_owed=3000.75),       │
│   DelinquentRow(lease_id=12347, amount_owed=0.00),          │
│   ... [33 more] ...                                         │
│ ]                                                            │
└──────────────────────────────────────────────────────────────┘
                            ↓
        [Sort by amount_owed, descending]
                            ↓
┌──────────────────────────────────────────────────────────────┐
│ After Sort (Descending by amount_owed):                     │
│                                                              │
│ results = [                                                  │
│   DelinquentRow(lease_id=12345, amount_owed=5000.50),       │
│   DelinquentRow(lease_id=12348, amount_owed=3000.75),       │
│   DelinquentRow(lease_id=12346, amount_owed=1200.00),       │
│   DelinquentRow(lease_id=12347, amount_owed=0.00),          │
│   ... [33 more, sorted descending] ...                      │
│ ]                                                            │
│                                                              │
│ [TRIM TO max_rows if exceeded]                              │
│ len(results) = 37                                            │
└──────────────────────────────────────────────────────────────┘
                            ↓
              Return: (results, leases_scanned=2100)
File: src/collections_sync/fetch.py (lines 196-206)
```

---

### Stage 7: Transform to Sheet Values
```
┌──────────────────────────────────────────────────────────────┐
│ Input: list[DelinquentRow]                                  │
│                                                              │
│ [                                                            │
│   DelinquentRow(                                             │
│     lease_id=12345,                                          │
│     name="John Doe",                                         │
│     address="123 Main St",                                   │
│     phone="555-1234",                                        │
│     email="john.doe@example.com",                            │
│     amount_owed=5000.50,                                     │
│     date_added="04/23/2026"                                  │
│   ),                                                         │
│   ... [36 more] ...                                         │
│ ]                                                            │
│                                                              │
│ HEADERS (27 columns):                                       │
│ [0]  = "Date First Added"         ← OWNED                   │
│ [1]  = "Name"                     ← OWNED                   │
│ [2]  = "Address:"                 ← OWNED                   │
│ [3]  = "Phone Number"             ← OWNED                   │
│ [4]  = "Email"                    ← OWNED                   │
│ [5]  = "Amount Owed:"             ← OWNED                   │
│ [6]  = "Date of 5 Day:"           ← PRESERVED               │
│ ...                                                          │
│ [14] = "Last Edited Date"         ← OWNED                   │
│ ...                                                          │
│ [24] = "Lease ID"                 ← OWNED (KEY)             │
│ ...                                                          │
│ [26] = "Date Status Changed..."   ← PRESERVED               │
└──────────────────────────────────────────────────────────────┘
                            ↓
           [For each DelinquentRow, create array]
           [Initialize with 27 "None" values]
           [Map owned fields to columns by index]
           [Leave preserved columns as ""]
           [Set Last Edited Date to TODAY]
                            ↓
┌──────────────────────────────────────────────────────────────┐
│ Output: list[list[Any]] (37 rows × 27 columns)             │
│                                                              │
│ [                                                            │
│   [                                                          │
│     "04/23/2026",              # [0]  Date First Added      │
│     "John Doe",                # [1]  Name                  │
│     "123 Main St",             # [2]  Address               │
│     "555-1234",                # [3]  Phone Number          │
│     "john.doe@example.com",    # [4]  Email                 │
│     5000.50,                   # [5]  Amount Owed           │
│     "",                        # [6]  Date of 5 Day (empty) │
│     "",                        # [7]  Expired Lease (empty) │
│     "",                        # [8]  Returned Payment      │
│     "",                        # [9]  Date of Next Payment  │
│     "",                        # [10] Date of Last payment  │
│     "",                        # [11] Payment Plan Details  │
│     "",                        # [12] Missed Payment Plan   │
│     "",                        # [13] Remarks               │
│     "04/23/2026",              # [14] Last Edited Date      │
│     "",                        # [15] Status (to be manual) │
│     "",                        # [16-23] Call tracking...   │
│     12345,                     # [24] Lease ID              │
│     "",                        # [25] Phone Number (dup)    │
│     ""                         # [26] Date Status Changed   │
│   ],                                                         │
│   [                                                          │
│     "04/23/2026", "Jane Smith", "456 Oak Ave", ..., 12346   │
│   ],                                                         │
│   ... [35 more rows] ...                                    │
│ ]                                                            │
│                                                              │
│ Each row = 27 elements                                      │
│ Total rows = 37                                             │
│ Ready for Google Sheets API                                 │
└──────────────────────────────────────────────────────────────┘
File: src/collections_sync/transform.py (lines 36-91)
```

---

### Stage 8: Read Existing Sheet Data
```
┌──────────────────────────────────────────────────────────────┐
│ Google Sheets API Call:                                     │
│                                                              │
│ GET /v4/spreadsheets/{SHEET_ID}/values/Collections!A1:ZZ1   │
│ [Read Header Row]                                           │
│                                                              │
│ GET /v4/spreadsheets/{SHEET_ID}/values/Collections!A2:Z50000
│ [Read Existing Data - Up to 50K rows]                       │
└──────────────────────────────────────────────────────────────┘
                            ↓
                  [Parse & Cache]
                            ↓
┌──────────────────────────────────────────────────────────────┐
│ Existing Data in Memory:                                    │
│                                                              │
│ headers = [                                                  │
│   "Date First Added", "Name", "Address:", ..., "Lease ID", │
│   ...                                                        │
│ ]                                                            │
│ num_cols = 27                                                │
│                                                              │
│ existing_rows = [                                            │
│   [                                                          │
│     "04/20/2026",              # Existing row for Lease 12346
│     "Jane Smith",                                            │
│     "456 Oak Ave",                                           │
│     "555-5678",                                              │
│     "jane@test.com",                                         │
│     1200.00,                                                 │
│     "5 day sent",              ← MANUAL ENTRY by staff      │
│     "No",                       ← MANUAL ENTRY by staff      │
│     "",                                                      │
│     "Call on Tuesday",          ← MANUAL ENTRY by staff      │
│     "",                                                      │
│     "Payment plan agreed",      ← MANUAL ENTRY by staff      │
│     "Court date 5/15",          ← MANUAL ENTRY by staff      │
│     "Send blue notice",         ← MANUAL ENTRY by staff      │
│     "04/20/2026",              # Last Edited Date           │
│     "Active Negotiations",      ← MANUAL ENTRY by staff      │
│     "04/18/2026",              ← MANUAL ENTRY by staff      │
│     "04/19/2026",              ← MANUAL ENTRY by staff      │
│     "",                                                      │
│     "",                                                      │
│     "",                                                      │
│     "04/22/2026",              ← MANUAL ENTRY by staff      │
│     "",                                                      │
│     "04/21/2026",              ← MANUAL ENTRY by staff      │
│     12346,                     # Lease ID (KEY)             │
│     "555-5678",                # Phone Number (dup)         │
│     "05/15/2026"               ← MANUAL ENTRY by staff      │
│   ],                                                         │
│   [                                                          │
│     "04/15/2026", "Bob Johnson", "789 Pine St", ..., 12347  │
│   ],                                                         │
│   ... [39 more existing rows] ...                           │
│ ]                                                            │
│                                                              │
│ existing_by_key = {                                          │
│   "12346": [...existing row data...],                        │
│   "12347": [...existing row data...],                        │
│   ... [40 more] ...                                         │
│ }                                                            │
│                                                              │
│ key_to_row_num = {                                           │
│   "12346": 2,    ← Row 2 in sheet                           │
│   "12347": 3,    ← Row 3 in sheet                           │
│   ... [40 more] ...                                         │
│ }                                                            │
└──────────────────────────────────────────────────────────────┘
File: src/collections_sync/sheets_writer.py (lines 109-180)
```

---

### Stage 9: Merge Logic (THE CRITICAL STEP)
```
┌──────────────────────────────────────────────────────────────┐
│ NEW ROW (from Buildium):                                    │
│                                                              │
│ [                                                            │
│   "04/23/2026",            # [0]  Date First Added (NEW)   │
│   "Jane Smith",            # [1]  Name (NEW - same)         │
│   "456 Oak Ave",           # [2]  Address (NEW - same)      │
│   "555-5678",              # [3]  Phone (NEW - same)        │
│   "jane@test.com",         # [4]  Email (NEW - same)        │
│   1500.00,                 # [5]  Amount Owed (NEW - +300!) │
│   "",                      # [6]  Date of 5 Day (empty)    │
│   "",                      # [7]  Expired Lease (empty)    │
│   "",                      # [8]  Returned Payment (empty) │
│   "",                      # [9]  (empty)                  │
│   "",                      # [10] (empty)                  │
│   "",                      # [11] (empty)                  │
│   "",                      # [12] (empty)                  │
│   "",                      # [13] (empty)                  │
│   "04/23/2026",            # [14] Last Edited Date (NEW)   │
│   "",                      # [15] Status (empty - old: ...) │
│   "",                      # [16] (empty)                  │
│   "",                      # [17] (empty)                  │
│   "",                      # [18] (empty)                  │
│   "",                      # [19] (empty)                  │
│   "",                      # [20] (empty)                  │
│   "",                      # [21] (empty)                  │
│   "",                      # [22] (empty)                  │
│   "",                      # [23] (empty)                  │
│   12346,                   # [24] Lease ID (KEY)           │
│   "",                      # [25] (empty)                  │
│   ""                       # [26] (empty)                  │
│ ]                                                            │
│                                                              │
│ ==================== MERGE LOGIC =======================   │
│                                                              │
│ IF Lease 12346 EXISTS in key_to_row_num:                    │
│   ├─ Copy existing row (to preserve manual entries)         │
│   ├─ For OWNED columns only:                                │
│   │   └─ Overwrite with NEW values                          │
│   ├─ For PRESERVED columns:                                 │
│   │   └─ Keep EXISTING values                               │
│   └─ SPECIAL: Never overwrite "Date First Added" if set     │
│                                                              │
│ ELSE:                                                        │
│   └─ This is a NEW lease, keep new row as-is               │
└──────────────────────────────────────────────────────────────┘
                            ↓
         [Compare & Decide: Update or Append]
                            ↓
┌──────────────────────────────────────────────────────────────┐
│ MERGED ROW (Result of merge for Lease 12346):              │
│                                                              │
│ [                                                            │
│   "04/20/2026",            # [0]  Date First Added          │
│                            #      ↑ PRESERVED (never        │
│                            #      overwrite this!)          │
│   "Jane Smith",            # [1]  Name (UPDATED)            │
│   "456 Oak Ave",           # [2]  Address (UPDATED)         │
│   "555-5678",              # [3]  Phone (UPDATED)           │
│   "jane@test.com",         # [4]  Email (UPDATED)           │
│   1500.00,                 # [5]  Amount Owed (UPDATED!)    │
│   "5 day sent",            # [6]  Date of 5 Day             │
│                            #      ↑ PRESERVED (manual keep) │
│   "No",                    # [7]  Expired Lease             │
│                            #      ↑ PRESERVED (manual keep) │
│   "",                      # [8]  Returned Payment (kept)   │
│   "Call on Tuesday",       # [9]  (PRESERVED - manual!)     │
│   "",                      # [10] (empty)                   │
│   "Payment plan agreed",   # [11] (PRESERVED - manual!)     │
│   "Court date 5/15",       # [12] (PRESERVED - manual!)     │
│   "Send blue notice",      # [13] (PRESERVED - manual!)     │
│   "04/23/2026",            # [14] Last Edited Date          │
│                            #      ↑ UPDATED (today)         │
│   "Active Negotiations",   # [15] Status (PRESERVED!)       │
│   "04/18/2026",            # [16] (PRESERVED)               │
│   "04/19/2026",            # [17] (PRESERVED)               │
│   "",                      # [18] (kept)                    │
│   "",                      # [19] (kept)                    │
│   "",                      # [20] (kept)                    │
│   "04/22/2026",            # [21] (PRESERVED - manual!)     │
│   "",                      # [22] (kept)                    │
│   "04/21/2026",            # [23] (PRESERVED - manual!)     │
│   12346,                   # [24] Lease ID (KEY - unchanged)│
│   "555-5678",              # [25] (PRESERVED)               │
│   "05/15/2026"             # [26] (PRESERVED - manual!)     │
│ ]                                                            │
│                                                              │
│ KEY CHANGE: Amount Owed 1200.00 → 1500.00                   │
│ ALL MANUAL ENTRIES PRESERVED (columns 6-13, 15-23, 25-26)!  │
└──────────────────────────────────────────────────────────────┘
File: src/collections_sync/sheets_writer.py (lines 188-242)
```

---

### Stage 10: Separate Updates vs Appends
```
┌──────────────────────────────────────────────────────────────┐
│ After merging all 37 rows:                                  │
│                                                              │
│ UPDATE list (20 rows):                                      │
│ [                                                            │
│   {                                                          │
│     "range": "Collections!A2:Z2",                            │
│     "values": [[merged_row for lease 12346]]                 │
│   },                                                         │
│   {                                                          │
│     "range": "Collections!A3:Z3",                            │
│     "values": [[merged_row for lease 12347]]                 │
│   },                                                         │
│   ... [18 more existing leases being updated] ...            │
│ ]                                                            │
│                                                              │
│ APPEND list (17 rows):                                      │
│ [                                                            │
│   [merged_row for NEW lease 12355],                          │
│   [merged_row for NEW lease 12356],                          │
│   ... [15 more new leases] ...                              │
│ ]                                                            │
│                                                              │
│ Append Target Range:                                        │
│   start_row = max(existing_row_numbers) + 1 = 42             │
│   end_row = 42 + 17 - 1 = 58                                 │
│   append_a1 = "Collections!A42:Z58"                          │
└──────────────────────────────────────────────────────────────┘
File: src/collections_sync/sheets_writer.py (lines 242-273)
```

---

### Stage 11: API Batch Update
```
┌──────────────────────────────────────────────────────────────┐
│ Google Sheets API Call:                                     │
│                                                              │
│ PUT /v4/spreadsheets/{SHEET_ID}/values:batchUpdate          │
│                                                              │
│ Request Body:                                               │
│ {                                                            │
│   "data": [                                                  │
│     {                                                        │
│       "range": "Collections!A2:Z2",                          │
│       "values": [                                            │
│         [                                                    │
│           "04/20/2026", "Jane Smith", "456 Oak Ave",        │
│           "555-5678", "jane@test.com", 1500.00,             │
│           "5 day sent", "No", "", "Call on Tuesday", ...,   │
│           "04/23/2026", "Active Negotiations", ..., 12346   │
│         ]                                                    │
│       ]                                                      │
│     },                                                       │
│     {                                                        │
│       "range": "Collections!A3:Z3",                          │
│       "values": [[...merged row for lease 12347...]]         │
│     },                                                       │
│     ... [18 more updates] ...                                │
│   ],                                                         │
│   "valueInputOption": "USER_ENTERED"                        │
│ }                                                            │
│                                                              │
│ [CHUNKED: 200 ranges per API call, 150ms pause between]     │
│ Execution:                                                   │
│   API Call 1: Updates 1-20 (all 20 fit)                     │
│   pause 150ms                                               │
│   [No more updates, move to appends]                         │
│                                                              │
│ Response:                                                    │
│ {                                                            │
│   "spreadsheetId": "...",                                    │
│   "totalUpdatedRows": 20,                                    │
│   "totalUpdatedColumns": 27,                                 │
│   "totalUpdatedCells": 540,                                  │
│   "responses": [...]                                        │
│ }                                                            │
└──────────────────────────────────────────────────────────────┘
File: src/collections_sync/sheets_writer.py (lines 274-286)
```

---

### Stage 12: API Write (Append) New Rows
```
┌──────────────────────────────────────────────────────────────┐
│ Google Sheets API Call:                                     │
│                                                              │
│ PUT /v4/spreadsheets/{SHEET_ID}/values/Collections!A42:Z58  │
│                                                              │
│ Request Body:                                               │
│ {                                                            │
│   "range": "Collections!A42:Z58",                            │
│   "values": [                                                │
│     [                                                        │
│       "04/23/2026", "John Doe", "123 Main St", ..., 12355   │
│     ],                                                       │
│     [                                                        │
│       "04/23/2026", "Alice Brown", "321 Elm St", ..., 12356 │
│     ],                                                       │
│     ... [15 more new rows] ...                              │
│   ],                                                         │
│   "majorDimension": "ROWS"                                   │
│ }                                                            │
│                                                              │
│ Response:                                                    │
│ {                                                            │
│   "spreadsheetId": "...",                                    │
│   "updatedRange": "Collections!A42:Z58",                     │
│   "updatedRows": 17,                                         │
│   "updatedColumns": 27,                                      │
│   "updatedCells": 459                                        │
│ }                                                            │
└──────────────────────────────────────────────────────────────┘
File: src/collections_sync/sheets_writer.py (lines 287-297)
```

---

### Stage 13: Apply Yellow Background to New Rows
```
┌──────────────────────────────────────────────────────────────┐
│ Two-step process:                                           │
│                                                              │
│ Step 1: Get Numeric Sheet ID                                │
│   GET /v4/spreadsheets/{SHEET_ID}                           │
│   Response: { "sheets": [ {"properties": {"sheetId": 0}} ]} │
│   → sheetId = 0                                              │
│                                                              │
│ Step 2: Apply background color via batchUpdate              │
│   POST /v4/spreadsheets/{SHEET_ID}:batchUpdate              │
│                                                              │
│   Request Body:                                             │
│   {                                                          │
│     "requests": [                                            │
│       {                                                      │
│         "updateCells": {                                     │
│           "range": {                                         │
│             "sheetId": 0,                                    │
│             "startRowIndex": 41,     # Rows 42-58 (0-indexed)
│             "endRowIndex": 58,                               │
│             "startColumnIndex": 0,   # All columns          │
│             "endColumnIndex": 27                             │
│           },                                                 │
│           "rows": [                                          │
│             {                                                │
│               "values": [                                    │
│                 {                                            │
│                   "userEnteredFormat": {                     │
│                     "backgroundColor": {                     │
│                       "red": 1.0,                            │
│                       "green": 0.98,                         │
│                       "blue": 0.8                            │
│                     }                                        │
│                   }                                          │
│                 },                                           │
│                 ... [26 more cells for the row] ...          │
│               ]                                              │
│             },                                               │
│             ... [16 more rows] ...                           │
│           ],                                                 │
│           "fields": "userEnteredFormat.backgroundColor"      │
│         }                                                    │
│       }                                                      │
│     ]                                                        │
│   }                                                          │
│                                                              │
│   Color RGB: (1.0, 0.98, 0.8) = Light yellow               │
│                                                              │
│ Result: Rows 42-58 now have yellow background ✨           │
└──────────────────────────────────────────────────────────────┘
File: src/collections_sync/sheets_writer.py (lines 299-314)
```

---

### Stage 14: HTTP Response
```
┌──────────────────────────────────────────────────────────────┐
│ SyncResult object constructed:                              │
│                                                              │
│ SyncResult(                                                  │
│   mode="bulk",                                               │
│   existing_keys=42,                                          │
│   rows_prepared=37,                                          │
│   rows_updated=20,                                           │
│   rows_appended=17,                                          │
│   leases_scanned=2100                                        │
│ )                                                            │
└──────────────────────────────────────────────────────────────┘
                            ↓
                    [Serialize to JSON]
                            ↓
┌──────────────────────────────────────────────────────────────┐
│ HTTP Response (200 OK)                                      │
│                                                              │
│ {                                                            │
│   "mode": "bulk",                                            │
│   "existing_keys": 42,                                       │
│   "rows_prepared": 37,                                       │
│   "rows_updated": 20,                                        │
│   "rows_appended": 17,                                       │
│   "leases_scanned": 2100                                     │
│ }                                                            │
│                                                              │
│ Timing:                                                     │
│   - Buildium API calls: ~15s (paginated, throttled)         │
│   - Concurrent enrichment: ~30s (3 workers, 250ms throttle) │
│   - Google Sheets reads: ~2s                                │
│   - Merge logic: <1s                                        │
│   - Google Sheets writes: ~5s                               │
│   ─────────────────────────────                             │
│   - Total: ~53s for bulk sync of 37 rows                    │
│                                                              │
│ Interpretation:                                             │
│   ✓ 20 existing rows were updated with new balances        │
│   ✓ 17 new rows were added (and highlighted yellow)        │
│   ✓ 2100 leases were scanned from Buildium                 │
│   ✓ 42 previously-tracked leases remain in the sheet       │
│   ✓ Manual staff entries were preserved!                   │
└──────────────────────────────────────────────────────────────┘
File: src/collections_sync/app.py (lines 176-185)
```

---

## Data Transformation Summary Table

| Stage | Input Type | Output Type | Key Operation |
|-------|-----------|------------|---|
| 1 | JSON (HTTP) | SyncRequest | Pydantic validation |
| 2 | Buildium JSON | dict[int, float] | Pagination + flatten |
| 3 | Buildium JSON | list[Lease] | Parse to dataclass |
| 4 | list[Lease] + dict | list[Task] | Filter by balance/existing |
| 5 | Task + TenantDetails | DelinquentRow | Merge fields, pick tenant |
| 6 | list[DelinquentRow] | list[DelinquentRow] | Sort descending by amount |
| 7 | DelinquentRow | list[list[Any]] | Expand to 27 columns |
| 8 | Sheet JSON | dict[key: list] | Index by lease_id |
| 9 | New arrays + Existing | Merged arrays | Preserve manual entries |
| 10 | Merged arrays | Update + Append lists | Separate by existence |
| 11 | Updates | API response | batchUpdate call |
| 12 | Appends | API response | write call |
| 13 | Row range | API response | batchUpdate (formatting) |
| 14 | Results | SyncResult JSON | Serialize + HTTP return |

---

## Critical Design Decisions

1. **3-Worker Semaphore** — Limits concurrent Buildium API calls to 3 to avoid rate limiting
2. **250ms Throttle** — Added delay before each tenant API call to spread requests
3. **Column Preservation** — Only OWNED columns updated; preserved columns never overwritten
4. **Date First Added Lock** — Never overwritten once set (tracks when debt first appeared)
5. **Existing Key Caching** — Sheet keys read once, used to filter Buildium fetches
6. **Yellow Highlighting** — New rows visually distinguish from existing data
7. **Batch Updates** — 200-row chunks with 150ms pause to avoid API quota issues

---

## Error Scenarios & Payloads

### Scenario: Buildium API Rate Limit (429)
```
BuildiumClient catches 429 response
→ Exponential backoff: 2s, 4s, 8s, 16s, 32s (max 5 retries)
→ If all retries fail: Exception bubbles to app.py
→ HTTP 500 response with error detail
→ Cloud Scheduler can retry the entire sync request
```

### Scenario: No Active Tenant Found
```python
DelinquentRow(
    lease_id=12345,
    name="(no active tenant found)",  # ← Placeholder
    address=lease.unit.address,       # Fallback to unit
    phone="",
    email="",
    amount_owed=5000.50,
    date_added="04/23/2026"
)
```

### Scenario: Tenant Lookup Failure
```python
DelinquentRow(
    lease_id=12345,
    name="(tenant lookup failed)",    # ← Error marker
    address=lease.unit.address,       # Fallback to unit
    phone="",
    email="",
    amount_owed=5000.50,
    date_added="04/23/2026"
)
```

---

## Quick Reference: File Locations

| Payload Stage | File | Lines |
|---|---|---|
| HTTP Request | `app.py` | 107-130 |
| SyncRequest Model | `models.py` | 28-32 |
| Buildium Fetch | `fetch.py` | 90-96 |
| Lease Fetch | `fetch.py` | 97-102 |
| Enrichment Loop | `fetch.py` | 103-195 |
| DelinquentRow Creation | `fetch.py` | 160-186 |
| Sort | `fetch.py` | 196-206 |
| Transform | `transform.py` | 36-91 |
| Sheet Read | `sheets_writer.py` | 109-180 |
| Merge Logic | `sheets_writer.py` | 188-242 |
| Updates/Appends | `sheets_writer.py` | 242-297 |
| Formatting | `sheets_writer.py` | 299-314 |
| Response | `app.py` | 176-185 |

