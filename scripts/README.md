# Testing Scripts

Temporary testing infrastructure for validating robustness features.

**Note:** These files are NOT committed to GitHub. Clean them up after testing is complete.

## Files

| File | Purpose |
|------|---------|
| `.env.phase1` | Environment config for Phase 1 (robustness disabled) |
| `.env.phase2` | Environment config for Phase 2 (robustness enabled) |
| `run_all_tests.sh` | Main test orchestration script (runs all 4 phases) |

## Quick Start

```bash
# 1. Configure environment files with your credentials
nano .env.phase1
nano .env.phase2

# 2. Run all tests
bash run_all_tests.sh

# 3. Monitor output and follow checklist
# See ../TESTING_QUICKSTART.md for details

# 4. Review results
ls -lh ../.test_logs/

# 5. Clean up
rm -rf ../.test_logs/
```

## What Gets Tested

- **Phase 1:** Baseline (service without robustness)
- **Phase 2:** Robustness enabled (locking, atomic, verification)
- **Phase 3:** Error scenarios (user-friendly vs. debug responses)
- **Phase 4:** Performance metrics (overhead measurement)

## Credentials Needed

Before running, prepare:

- `BUILDIUM_KEY` — Buildium API client ID
- `BUILDIUM_SECRET` — Buildium API client secret
- `SHEET_ID` — Test Google Sheet ID (separate from production)
- `GOOGLE_SHEETS_CREDENTIALS_PATH` — Path to credentials JSON (optional, can use ADC)

## Output

All test results saved to `.test_logs/`:

```
.test_logs/
├── phase_1_service.log         # Service logs for Phase 1
├── phase_1_*.json              # Test responses
├── phase_2_service.log         # Service logs for Phase 2
├── phase_2_*.json              # Test responses
├── phase_3_*.json              # Error scenario responses
├── perf_baseline_*.json        # Performance baseline
├── perf_baseline_*.time        # Timing for baseline
├── perf_robustness_*.json      # Performance with robustness
└── perf_robustness_*.time      # Timing with robustness
```

## Cleanup

```bash
# Remove all test artifacts
rm -rf ../.test_logs/

# Optionally keep the .env files for re-testing
# Or remove them: rm .env.phase1 .env.phase2
```

## References

- Full test guide: `../TESTING_QUICKSTART.md`
- Sign-off checklist: `../TESTING_CHECKLIST.md`
- Code being tested: `../src/collections_sync/`
