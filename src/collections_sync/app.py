"""FastAPI application for collections-sync service."""
import logging
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import datetime

from fastapi import FastAPI, HTTPException
from core_integrations.buildium import BuildiumClient, BuildiumConfig
from core_integrations.google_sheets import GoogleSheetsClient, GoogleSheetsConfig

from .config import CollectionsSyncConfig
from .fetch import fetch_active_owed_rows
from .models import SyncMode, SyncRequest, SyncResult
from .sheets_writer import CollectionsSheetsWriter

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize and cleanup on app startup/shutdown."""
    logger.info("Starting collections-sync service")

    cfg = CollectionsSyncConfig()
    cfg.validate_required()

    # Initialize Buildium client
    buildium_config = BuildiumConfig(
        enabled=True,
        base_url=cfg.buildium_base_url,
        client_id=cfg.buildium_key,
        client_secret=cfg.buildium_secret,
    )
    buildium_client = BuildiumClient(buildium_config)

    # Initialize Google Sheets client
    sheets_config = GoogleSheetsConfig()
    sheets_client = GoogleSheetsClient(sheets_config)

    # Store in app state
    app.state.cfg = cfg
    app.state.buildium = buildium_client
    app.state.sheets = sheets_client

    logger.info(
        "Service ready: sheet=%s tab=%s port=%d",
        cfg.effective_sheet_id,
        cfg.worksheet_name,
        cfg.port,
    )

    yield

    logger.info("Shutting down collections-sync service")


app = FastAPI(
    title="Collections Sync",
    description="Sync delinquent tenant data to Google Sheets",
    version="0.1.0",
    lifespan=lifespan,
)


@app.get("/")
async def health() -> dict:
    """Health check endpoint."""
    return {"status": "ok"}


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
    except Exception as e:
        logger.error("Sync failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


async def _run_bulk(
    cfg: CollectionsSyncConfig,
    buildium: BuildiumClient,
    sheets: GoogleSheetsClient,
    request: SyncRequest,
) -> dict:
    """Execute bulk sync mode."""
    logger.info("Starting bulk sync: max_pages=%d, max_rows=%d", request.max_pages, request.max_rows)

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

    logger.info(
        "Fetched %d delinquent rows from %d leases",
        len(rows),
        leases_scanned,
    )

    if not rows:
        logger.info("No rows to sync")
        return asdict(SyncResult(
            mode=request.mode.value,
            existing_keys=len(existing_lease_ids),
            rows_prepared=0,
            leases_scanned=leases_scanned,
        ))

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
        return asdict(SyncResult(
            mode=request.mode.value,
            existing_keys=0,
        ))

    logger.info("Found %d existing leases in sheet", len(key_to_row))

    # Fetch only balances for these leases
    existing_lease_ids = [int(k) for k in key_to_row.keys() if k.isdigit()]

    logger.info("Fetching balances for %d leases", len(existing_lease_ids))

    balances = await _fetch_balances(buildium, existing_lease_ids)

    logger.info("Fetched balances for %d leases", len(balances))

    # Update balances
    rows_updated = writer.quick_update_balances(key_to_row, sheet_headers, balances)

    logger.info("Quick sync complete: %d rows updated", rows_updated)

    return asdict(SyncResult(
        mode=request.mode.value,
        existing_keys=len(key_to_row),
        rows_updated=rows_updated,
    ))


async def _fetch_balances(client: BuildiumClient, lease_ids: list[int]) -> dict[int, float]:
    """Fetch balances for specific leases.

    Args:
        client: BuildiumClient.
        lease_ids: List of lease IDs to fetch balances for.

    Returns:
        Map of lease_id -> balance.
    """
    def _do_fetch():
        return client.fetch_outstanding_balances_for_lease_ids(lease_ids)

    import asyncio
    return await asyncio.to_thread(_do_fetch)
