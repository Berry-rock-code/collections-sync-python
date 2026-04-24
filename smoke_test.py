#!/usr/bin/env python3
"""Smoke test: sync real Buildium collections data to the real Collections Status sheet."""
import sys
import json
import asyncio
from pathlib import Path

# Load credentials from auth directory
auth_dir = Path("/home/jake/code/BRH/auth")
env_file = auth_dir / ".env"

if not env_file.exists():
    print(f"ERROR: {env_file} not found")
    sys.exit(1)

# Parse .env manually
env_vars = {}
with open(env_file) as f:
    for line in f:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            k, v = line.split("=", 1)
            env_vars[k] = v

# Load config from env vars
import os
for k, v in env_vars.items():
    os.environ[k] = v

print("=" * 80)
print("COLLECTIONS-SYNC SMOKE TEST")
print("=" * 80)
print(f"Sheet ID: {env_vars.get('SHEET_ID', 'NOT SET')}")
print(f"Worksheet: {env_vars.get('WORKSHEET_NAME', 'NOT SET')}")
print()

# Now import collections_sync modules (which read from os.environ)
from collections_sync.config import CollectionsSyncConfig
from collections_sync.models import SyncRequest, SyncMode
from core_integrations.buildium import BuildiumClient, BuildiumConfig
from core_integrations.google_sheets import GoogleSheetsClient, GoogleSheetsConfig

# Initialize clients
config = CollectionsSyncConfig()
print(f"Config loaded: sheet_id={config.sheet_id}, worksheet={config.worksheet_name}")
print()

# BuildiumConfig reads from BUILDIUM_* env vars
buildium_config = BuildiumConfig()
buildium_client = BuildiumClient(buildium_config)

google_config = GoogleSheetsConfig(credentials_path=config.google_sheets_credentials_path)
google_client = GoogleSheetsClient(google_config)

print("✓ Buildium client initialized")
print("✓ Google Sheets client initialized")
print()

# Test 1: Fetch outstanding balances
print("Test 1: Fetch outstanding balances...")
try:
    balances = buildium_client.fetch_outstanding_balances()
    print(f"✓ Fetched {len(balances)} leases with balances")
    if balances:
        # Show first 3
        for lease_id, amount in list(balances.items())[:3]:
            print(f"  - Lease {lease_id}: ${amount:.2f}")
except Exception as e:
    print(f"✗ FAILED: {e}")
    sys.exit(1)

print()

# Test 2: List leases
print("Test 2: List all leases...")
try:
    leases = buildium_client.list_all_leases()
    print(f"✓ Fetched {len(leases)} leases")
except Exception as e:
    print(f"✗ FAILED: {e}")
    sys.exit(1)

print()

# Test 3: Run async fetch_active_owed_rows
print("Test 3: Concurrent tenant enrichment (max 5 rows for quick test)...")
try:
    from collections_sync.fetch import fetch_active_owed_rows

    rows, leases_scanned = asyncio.run(
        fetch_active_owed_rows(
            client=buildium_client,
            max_pages=0,
            max_rows=5,  # Limit to 5 for quick test
            existing_lease_ids=set(),
        )
    )
    print(f"✓ Enriched {len(rows)} rows (scanned {leases_scanned} leases)")
    if rows:
        for row in rows[:3]:
            print(f"  - {row.name}: Lease {row.lease_id}, owes ${row.amount_owed:.2f}")
except Exception as e:
    print(f"✗ FAILED: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

print()

# Test 4: Sheet read check
print("Test 4: Read Collections Status sheet headers...")
try:
    google_client.ensure_sheet(config.sheet_id, config.worksheet_name)
    headers_raw = google_client.read_range(config.sheet_id, f"{config.worksheet_name}!A1:Z1")
    if headers_raw and headers_raw[0]:
        headers = [str(h).strip() for h in headers_raw[0] if h]
        print(f"✓ Sheet has {len(headers)} columns")
        print(f"  Columns: {', '.join(headers[:5])}...")
    else:
        print("✗ No headers found in sheet")
        sys.exit(1)
except Exception as e:
    print(f"✗ FAILED: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

print()
print("=" * 80)
print("ALL SMOKE TESTS PASSED ✓")
print("=" * 80)
print()
print("Service is ready to sync. To run a real sync:")
print(f"  POST http://localhost:8080/ with body:")
print(f'    {{"mode": "bulk", "max_pages": 0, "max_rows": 0}}')
print()
