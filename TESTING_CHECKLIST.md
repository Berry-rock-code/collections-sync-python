# Collections Sync Robustness Testing Checklist

## Pre-Testing Setup

Before running tests, complete these steps:

- [ ] **Credentials ready**
  - [ ] Buildium API key and secret
  - [ ] Google Sheets credentials JSON file
  - [ ] Test Google Sheet created (separate from production)

- [ ] **Environment files configured**
  ```bash
  # Edit these files with your credentials:
  # scripts/.env.phase1   (robustness disabled)
  # scripts/.env.phase2   (robustness enabled)
  ```

- [ ] **Service can start**
  ```bash
  cd /home/jake/code/BRH/collections-sync-python
  python -m collections_sync --help
  ```

---

## PHASE 1: Baseline Testing (Robustness DISABLED)

**Goal:** Verify the service works without robustness features (baseline behavior)

```bash
# Start phase 1 testing
bash scripts/run_all_tests.sh
# (This will run all phases, or you can run individually)
```

### Test 1.1: Health Check ✓

- [ ] Service starts successfully
- [ ] `GET /` returns `{"status": "ok"}`
- [ ] Service listens on port 8080

**Result:** _______________

### Test 1.2: Quick Sync ✓

- [ ] Quick sync completes (should be < 2 minutes)
- [ ] Rows are updated with new balances
- [ ] "Last Edited Date" is updated
- [ ] "Date First Added" is NOT changed on existing rows
- [ ] Response includes `rows_updated` count

**Time taken:** _______________
**Rows updated:** _______________
**Result:** _______________

### Test 1.3: Bulk Sync ✓

- [ ] Bulk sync completes (may take 2-10 minutes depending on data)
- [ ] New rows are added with yellow highlight
- [ ] Existing rows are merged (keep manual columns, update sync columns)
- [ ] Response includes `rows_prepared`, `rows_updated`, `rows_appended`, `leases_scanned`
- [ ] Sheet data looks correct (no missing values, no duplicates)

**Time taken:** _______________
**Rows prepared:** _______________
**Rows updated:** _______________
**Rows appended:** _______________
**Leases scanned:** _______________
**Result:** _______________

### Test 1.4: Concurrent Sync (Race Condition) ✓

This test shows the problem that robustness features solve.

```bash
# Terminal 1
curl -X POST http://localhost:8080/ \
  -H "Content-Type: application/json" \
  -d '{"mode": "bulk", "max_pages": 1, "max_rows": 100}' &

# Terminal 2 (start immediately after, before first completes)
sleep 5
curl -X POST http://localhost:8080/ \
  -H "Content-Type: application/json" \
  -d '{"mode": "bulk", "max_pages": 1, "max_rows": 100}'
```

- [ ] Both syncs complete (no errors)
- [ ] **Check sheet:** Which data is visible? Is it from sync 1, sync 2, or mixed?
- [ ] **Expected:** Likely mixed/corrupted (second overwrites first without locking)

**Data visible in sheet:** _______________
**Issue observed:** _______________
**Result:** EXPECTED RACE CONDITION SEEN (this is the baseline problem)

---

## PHASE 2: Robustness Enabled (Locking + Atomic + Verification)

**Goal:** Verify locking, atomic operations, and verification work correctly

### Test 2.1: Lock Tab Auto-Created ✓

- [ ] Check your Google Sheet
- [ ] New tab `_sync_lock` exists (auto-created by service)
- [ ] Tab has cell A1 (currently empty, as no lock is held)

**Result:** _______________

### Test 2.2: Single Sync with Robustness ✓

- [ ] Sync completes successfully
- [ ] Time taken is ~1-3 seconds longer than Phase 1 (locking + verification overhead)
- [ ] Lock was acquired and released (check logs)
- [ ] Verification passed (no DataCorruptionError)
- [ ] Data in sheet matches what was written

**Time taken (vs Phase 1):** _______________
**Overhead:** _____ seconds (acceptable if < 10%)
**Verification result:** _______________

### Test 2.3: Concurrent Sync with Locking ✓

This test shows robustness fixing the race condition.

```bash
# Terminal 1
curl -X POST http://localhost:8080/ \
  -H "Content-Type: application/json" \
  -d '{"mode": "bulk", "max_pages": 1, "max_rows": 100}' &
SYNC1_PID=$!

# Terminal 2 (start immediately after)
sleep 5
curl -X POST http://localhost:8080/ \
  -H "Content-Type: application/json" \
  -d '{"mode": "bulk", "max_pages": 1, "max_rows": 100}'

wait $SYNC1_PID
```

- [ ] **Sync 1:** Completes successfully (held lock, wrote data)
- [ ] **Sync 2:** Gets HTTP 503 with error:
  ```json
  {
    "error_type": "LockTimeoutError",
    "message": "Could not acquire sync lock within 30 seconds",
    "actions": ["1. Wait 30-60 seconds and retry", ...]
  }
  ```
- [ ] Sheet data is consistent (only Sync 1's data visible)
- [ ] No data loss, no overwrites

**Sync 1 status:** _______________
**Sync 2 status:** 503 LockTimeoutError (expected)
**Sheet consistency:** _______________
**Result:** ✓ LOCK WORKING CORRECTLY

### Test 2.4: Lock Lifecycle in Logs ✓

Check logs for lock events:

```bash
# In service logs (or .test_logs/phase_2_service.log):
grep "lock" .test_logs/phase_2_service.log

# Expected:
# - "Acquiring lock"
# - "Lock acquired successfully"
# - "Releasing lock"
# - "Lock released"
```

- [ ] Acquire log entry appears
- [ ] Release log entry appears
- [ ] Between acquire and release: sync happens

**Result:** _______________

---

## PHASE 3: Error Scenarios

**Goal:** Verify error messages are helpful and appropriate for audience

### Test 3.1: User-Friendly Error Response ✓

```bash
curl -X POST http://localhost:8080/ \
  -H "Content-Type: application/json" \
  -d '{"mode": "bulk", "max_pages": 0, "max_rows": 0}'
```

Expected response for a non-technical user:

```json
{
  "error_type": "...",
  "request_id": "...",
  "message": "...",
  "actions": [
    "1. ...",
    "2. ..."
  ]
}
```

- [ ] Response includes `actions` array (simple steps)
- [ ] No jargon (no "LockTimeoutError", no stack traces)
- [ ] `request_id` included for logging
- [ ] Error message is clear

**Response format:** User-friendly ✓
**Result:** _______________

### Test 3.2: Debug Error Response ✓

```bash
curl -X POST http://localhost:8080/?debug=true \
  -H "Content-Type: application/json" \
  -d '{"mode": "bulk", "max_pages": 0, "max_rows": 0}'
```

Expected response for DevOps/support:

```json
{
  "error_type": "...",
  "request_id": "...",
  "http_status": 500,
  "exception_type": "...",
  "stack_trace": "[full Python traceback]",
  "technical_info": {
    "reason": "...",
    "lock_sheet": "...",
    "spreadsheet_id": "..."
  }
}
```

- [ ] Response includes `stack_trace` (full Python traceback)
- [ ] Response includes `exception_type` (e.g., "DataCorruptionError")
- [ ] Response includes `technical_info` dict with details
- [ ] `http_status` included
- [ ] Suitable for debugging

**Response format:** Debug/Technical ✓
**Stack trace present:** Yes ✓
**Result:** _______________

### Test 3.3: Lock Timeout Error (503) ✓

Force a lock timeout by holding the lock:

```bash
# Terminal 1: Hold lock by making slow sync
curl -X POST http://localhost:8080/ \
  -H "Content-Type: application/json" \
  -d '{"mode": "bulk", "max_pages": 1, "max_rows": 200}' &

# Terminal 2: Try to sync while lock is held (within 30s)
sleep 15
curl -X POST http://localhost:8080/ \
  -H "Content-Type: application/json" \
  -d '{"mode": "bulk", "max_pages": 1, "max_rows": 50}'
```

- [ ] Gets HTTP 503 (Service Unavailable)
- [ ] Error type is "LockTimeoutError"
- [ ] Message says "could not acquire lock"
- [ ] User-mode includes suggestion to "wait and retry"
- [ ] Debug-mode includes lock_timeout_seconds and lock_stale_seconds

**HTTP status:** 503 ✓
**Error type:** LockTimeoutError ✓
**Result:** _______________

---

## PHASE 4: Performance Metrics

**Goal:** Measure overhead of robustness features

### Metric 4.1: Time Overhead

Run the test script which automatically measures both:

```bash
bash scripts/run_all_tests.sh
# Collects timing for both baseline and robustness runs
```

Compare results:

| Metric | Baseline (Phase 1) | With Robustness (Phase 2) | Overhead |
|--------|------------------|--------------------------|----------|
| Bulk sync time | ___ sec | ___ sec | ___ % |
| Quick sync time | ___ sec | ___ sec | ___ % |

- [ ] Overhead is acceptable (< 10% is ideal, < 20% is acceptable)
- [ ] Any overhead is due to: locking (1-2 API calls) + verification (1-2 reads)

**Acceptable:** _____ (Yes / No)
**Result:** _______________

### Metric 4.2: API Call Count

Count API calls using Google Sheets quota dashboard or logs:

| Operation | Phase 1 | Phase 2 | Difference |
|-----------|---------|---------|-----------|
| Bulk sync | ___ | ___ | +___ (lock + verify) |
| Quick sync | ___ | ___ | +___ (lock + verify) |

- [ ] Phase 2 has ~2-4 extra API calls per sync (lock + verify)
- [ ] This is acceptable for data consistency guarantee

**API calls:** _______________
**Acceptable:** _____ (Yes / No)
**Result:** _______________

### Metric 4.3: Memory/CPU

Monitor during a bulk sync:

```bash
# While sync is running:
ps aux | grep "python -m collections_sync"
# Check memory usage (RSS column)
```

- [ ] Memory usage is reasonable (< 500MB)
- [ ] CPU usage spikes during sync, returns to baseline after

**Memory used:** ___ MB
**CPU:** Reasonable _____ (Yes / No)
**Result:** _______________

---

## Sign-Off Checklist

Before deploying to production, confirm ALL of these:

### Functionality
- [ ] Phase 1 baseline tests pass (service works without robustness)
- [ ] Phase 2 robustness tests pass (locking works, no concurrent writes)
- [ ] Phase 3 error scenarios pass (user-friendly AND debug responses work)
- [ ] Lock tab auto-creates in sheet
- [ ] Concurrent syncs properly blocked with 503
- [ ] Data is consistent (no overwrites, no partial writes)

### Performance
- [ ] Time overhead acceptable (< 10% increase is good)
- [ ] API calls increased by expected amount (~2-4 per sync)
- [ ] Memory/CPU within reasonable bounds

### Data Integrity
- [ ] Sheet data matches what was written
- [ ] "Date First Added" never overwritten
- [ ] No duplicate rows
- [ ] No missing columns
- [ ] Yellow highlighting applied correctly

### Error Handling
- [ ] User-mode errors are simple and actionable
- [ ] Debug-mode errors include full technical details
- [ ] `request_id` present in all responses
- [ ] Lock timeout error (503) shown to users
- [ ] Validation error (422) shown when data is invalid
- [ ] Corruption error (500) shown when verification fails

### Logging
- [ ] All syncs logged with `request_id`
- [ ] Lock acquisition/release logged
- [ ] Verification results logged
- [ ] Error details logged with full context

---

## Issues Found

During testing, did you find any issues?

```
Issue 1:
Description: _______________________________
Severity: (Minor / Major / Critical)
Workaround: _______________________________
Resolution: _______________________________

Issue 2:
...
```

---

## Ready for Production?

**All sign-off items complete?** _____ (Yes / No)

**Any blocking issues?** _____ (Yes / No)

**Approval to deploy:**

Tester: _____________________ Date: __________
DevOps: _____________________ Date: __________

---

## After Deployment

Once deployed to production, monitor for 24 hours:

- [ ] No DataCorruptionErrors in logs
- [ ] Lock acquisition working smoothly (no excessive timeouts)
- [ ] Normal error rates
- [ ] Performance as expected (no slowdowns)
- [ ] Users report no issues

**24-hour monitoring complete:** _____ (Yes / No)
**Issues found:** _____
**Notes:** _____

---

## Cleanup

After testing is complete, clean up test files:

```bash
# Remove test logs
rm -rf .test_logs/

# Remove test environment files (optional, keep for re-testing)
# rm scripts/.env.phase1 scripts/.env.phase2
```

- [ ] Test files cleaned up
- [ ] This checklist saved (for audit trail)
