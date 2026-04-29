# Testing Quick Start

## TL;DR - Run This

```bash
# 1. Get your credentials
# (Buildium key + secret, Google Sheets credentials file)

# 2. Edit environment files
nano scripts/.env.phase1    # Add your credentials
nano scripts/.env.phase2    # Add your credentials

# 3. Run all tests
bash scripts/run_all_tests.sh

# 4. Follow the checklist
nano TESTING_CHECKLIST.md   # Check off each test as it passes

# 5. Review results
ls -lh .test_logs/
cat .test_logs/test_results.txt

# 6. Cleanup
rm -rf .test_logs/
```

---

## What Gets Tested

| Phase | What | Why | Time |
|-------|------|-----|------|
| 1 | Service works WITHOUT locking/atomic/verify | Baseline | 5-10 min |
| 2 | Locking + atomic + verify work correctly | Robustness | 5-10 min |
| 3 | Error messages are user-friendly AND debug-friendly | UX | 2 min |
| 4 | Performance impact is acceptable | Production-ready | 10 min |

**Total time: 20-35 minutes** (depends on your data volume)

---

## Step-by-Step

### Step 1: Prepare Credentials

Get these files ready:

```
Buildium:
  - API Key (BUILDIUM_KEY)
  - API Secret (BUILDIUM_SECRET)
  
Google Sheets:
  - Credentials JSON file (if not using Application Default Credentials)
  - Test Sheet ID (SHEET_ID) - must be separate from production
```

### Step 2: Configure Environment Files

Edit `scripts/.env.phase1`:

```bash
BUILDIUM_KEY=your_actual_key
BUILDIUM_SECRET=your_actual_secret
SHEET_ID=your_test_sheet_id
WORKSHEET_NAME=Collections Status
```

Copy to `scripts/.env.phase2`:

```bash
cp scripts/.env.phase1 scripts/.env.phase2

# Then edit scripts/.env.phase2 to add robustness:
SYNC_ENABLE_ATOMIC=true
SYNC_VERIFY_CHECKSUMS=true
```

### Step 3: Run Tests

```bash
cd /home/jake/code/BRH/collections-sync-python
bash scripts/run_all_tests.sh
```

The script will:
- Start service with Phase 1 config
- Run 4 tests
- Stop service
- Start service with Phase 2 config
- Run 4 more tests
- Collect performance metrics
- Save all results to `.test_logs/`

### Step 4: Watch the Tests

The script outputs in real-time. You'll see:

```
======================================================================
Collections Sync Robustness Testing Suite
======================================================================

✓ Prerequisites checked

======================================================================
PHASE 1: BASELINE TESTING (Robustness DISABLED)
======================================================================

Starting service with scripts/.env.phase1...
✓ Service started (PID: 12345)

--- Test: Health Check ---
{
  "status": "ok"
}
✓ Response saved...

--- Test: Quick Sync (balance updates) ---
...
```

### Step 5: Check Results

After each phase, review the output:

- Does it look correct?
- Are there any error messages?
- Are the row counts what you expected?

### Step 6: Manual Verification

Check your test Google Sheet after each phase:

**After Phase 1:**
- [ ] Sheet has new rows (yellow highlight)
- [ ] Existing rows updated with new balances
- [ ] Data looks correct

**After Phase 2:**
- [ ] `_sync_lock` tab exists
- [ ] Same data consistency as Phase 1
- [ ] No errors in responses

### Step 7: Review Performance

Check `.test_logs/`:

```bash
# Baseline times
cat .test_logs/perf_baseline_1.time
cat .test_logs/perf_baseline_2.time

# With robustness
cat .test_logs/perf_robustness_1.time
cat .test_logs/perf_robustness_2.time
```

Compare:
- Phase 1 took X seconds
- Phase 2 took Y seconds
- Overhead = (Y-X)/X % (should be < 10%)

### Step 8: Complete Checklist

Go through `TESTING_CHECKLIST.md` and check off each test:

```markdown
- [x] Test 1.1: Health Check ✓
- [x] Test 1.2: Quick Sync ✓
- [x] Test 1.3: Bulk Sync ✓
- [ ] Test 1.4: Concurrent Sync ✓
...
```

Fill in the values (time taken, rows affected, etc.)

### Step 9: Sign-Off

At the bottom of `TESTING_CHECKLIST.md`, sign off:

```markdown
Tester: Jake Kistler        Date: 2026-04-28
DevOps: ________________    Date: __________
```

### Step 10: Clean Up

```bash
# Remove test logs and temp files
rm -rf .test_logs/

# Keep the checklist for audit trail
# cp TESTING_CHECKLIST.md TESTING_CHECKLIST.2026-04-28.md
```

---

## What If Something Goes Wrong?

### Service won't start

```bash
# Check logs
tail -f .test_logs/phase_1_service.log

# Common issues:
# - Port 8080 already in use: change PORT in .env
# - Credentials invalid: check BUILDIUM_KEY, BUILDIUM_SECRET
# - Sheet not found: check SHEET_ID
# - Google credentials missing: check GOOGLE_SHEETS_CREDENTIALS_PATH
```

### Tests fail with errors

```bash
# Check detailed response
cat .test_logs/phase_1_*.json | jq '.'

# For HTTP errors:
# - 401: Credentials invalid
# - 404: Sheet not found
# - 422: Data validation error (expected in some cases)
# - 503: Lock timeout (expected when testing concurrent syncs)
# - 500: Actual error (check error_type in response)
```

### Timeout during bulk sync

The service is taking too long. Options:
1. Increase timeouts in `.env`:
   ```bash
   BAL_TIMEOUT=120
   LEASE_TIMEOUT=120
   TENANT_TIMEOUT=120
   ```

2. Reduce data volume:
   ```bash
   {"mode": "bulk", "max_pages": 1, "max_rows": 25}
   ```

3. Skip that test and continue

### Data in sheet looks wrong

1. Check the test sheet manually (go to Google Sheets)
2. Look for:
   - Duplicate rows
   - Missing data (empty columns)
   - Wrong lease IDs
   - Negative amounts

3. If critical issue found:
   - Document it
   - Restore sheet from backup
   - Mark "DO NOT DEPLOY" until fixed

---

## Next: Production Deployment

Once all tests pass and checklist is signed off:

```bash
# Update production environment
nano .env  # (NOT .env.test, the real .env)

# Set robustness to ON
SYNC_ENABLE_ATOMIC=true
SYNC_VERIFY_CHECKSUMS=true

# Deploy
gcloud run deploy collections-sync --source . --region us-central1

# Monitor for 24 hours
# Watch logs for errors
# If all good: announce to users
```

---

## Questions?

- Check logs: `.test_logs/phase_*_service.log`
- Check responses: `.test_logs/phase_*_*.json`
- Review checklist: `TESTING_CHECKLIST.md`
- Check code: `src/collections_sync/app.py` (error handlers)
