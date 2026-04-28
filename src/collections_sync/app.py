"""FastAPI application for collections-sync service."""
import json
import logging
import os
import tempfile
import uuid
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import datetime

from fastapi import FastAPI, HTTPException
from core_integrations.buildium import BuildiumClient, BuildiumConfig
from core_integrations.google_sheets import GoogleSheetsClient, GoogleSheetsConfig

from .async_utils import run_sync_with_timeout
from .config import CollectionsSyncConfig
from .data_validator import DataValidator
from .exceptions import DataCorruptionError, DataValidationError, LockTimeoutError
from .fetch import fetch_active_owed_rows
from .lock_manager import SyncLockManager
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

    # Handle Google Sheets credentials
    creds_path = None
    creds_json = os.getenv('GOOGLE_SHEETS_CREDS')
    if creds_json:
        try:
            # Write credentials from secret to temp file
            temp_file = tempfile.NamedTemporaryFile(
                mode='w',
                suffix='.json',
                delete=False,
                dir='/tmp'
            )
            temp_file.write(creds_json)
            temp_file.close()
            creds_path = temp_file.name
            logger.info("Wrote Google Sheets credentials to %s", creds_path)
        except Exception as e:
            logger.warning("Failed to write credentials file: %s", e)

    # Initialize Google Sheets client
    sheets_config = GoogleSheetsConfig()
    if creds_path:
        sheets_config.credentials_path = creds_path
        logger.info("Set credentials_path to %s", creds_path)
    logger.info("GoogleSheetsConfig credentials_path: %s", sheets_config.credentials_path)
    if sheets_config.credentials_path:
        logger.info("Credentials file exists: %s", os.path.exists(sheets_config.credentials_path))
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
        SyncResult as JSON with request_id for tracing.
    """
    cfg = app.state.cfg
    buildium = app.state.buildium
    sheets = app.state.sheets

    request_id = str(uuid.uuid4())
    logger.info("Sync requested request_id=%s mode=%s", request_id, request.mode)

    try:
        if request.mode == SyncMode.BULK:
            result = await _run_bulk(cfg, buildium, sheets, request, request_id)
        else:
            result = await _run_quick(cfg, buildium, sheets, request, request_id)

        result["request_id"] = request_id
        return result

    except LockTimeoutError as e:
        logger.warning("Lock timeout request_id=%s: %s", request_id, e)
        raise HTTPException(
            status_code=503,
            detail={
                "error_type": "LockTimeoutError",
                "request_id": request_id,
                "message": str(e),
                "suggestion": "Another sync is in progress. Retry in 30 seconds.",
            },
        )
    except DataValidationError as e:
        logger.error("Data validation error request_id=%s: %s", request_id, e)
        raise HTTPException(
            status_code=422,
            detail={
                "error_type": "DataValidationError",
                "request_id": request_id,
                "message": str(e),
                "suggestion": "Check source data quality in Buildium.",
            },
        )
    except DataCorruptionError as e:
        logger.error(
            "Data corruption detected request_id=%s: %s",
            request_id,
            e,
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail={
                "error_type": "DataCorruptionError",
                "request_id": request_id,
                "message": str(e),
                "suggestion": "Verify the sheet manually. The last sync may have partially written.",
            },
        )
    except Exception as e:
        logger.error("Unexpected error request_id=%s: %s", request_id, e, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={
                "error_type": "InternalError",
                "request_id": request_id,
                "message": str(e),
            },
        )


async def _run_bulk(
    cfg: CollectionsSyncConfig,
    buildium: BuildiumClient,
    sheets: GoogleSheetsClient,
    request: SyncRequest,
    request_id: str,
) -> dict:
    """Execute bulk sync mode."""
    logger.info(
        "Starting bulk sync request_id=%s: max_pages=%d, max_rows=%d",
        request_id,
        request.max_pages,
        request.max_rows,
    )

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
        return asdict(
            SyncResult(
                mode=request.mode.value,
                existing_keys=len(existing_lease_ids),
                rows_prepared=0,
                leases_scanned=leases_scanned,
            )
        )

    # Upsert to sheet
    if cfg.sync_enable_atomic:
        logger.info(
            "Using atomic upsert with locking request_id=%s", request_id
        )
        lock_mgr = SyncLockManager(
            client=sheets,
            spreadsheet_id=cfg.effective_sheet_id,
            lock_sheet=cfg.sync_lock_sheet,
            acquire_timeout=float(cfg.sync_lock_timeout_seconds),
            stale_timeout=float(cfg.sync_lock_stale_seconds),
        )

        validator = DataValidator()

        rows_updated, rows_appended = writer.upsert_preserving_atomic(
            new_rows=rows,
            lock_manager=lock_mgr,
            validator=validator,
            verify_checksums=cfg.sync_verify_checksums,
            max_retries=cfg.sync_max_retries,
            retry_backoff_ms=cfg.sync_retry_backoff_ms,
        )
    else:
        logger.info("Using legacy upsert (atomic disabled) request_id=%s", request_id)
        from .transform import HEADERS

        rows_updated, rows_appended = writer.upsert_preserving(HEADERS, rows)

    logger.info(
        "Upsert complete request_id=%s: %d updated, %d appended",
        request_id,
        rows_updated,
        rows_appended,
    )

    return asdict(
        SyncResult(
            mode=request.mode.value,
            existing_keys=len(existing_lease_ids),
            rows_prepared=len(rows),
            rows_updated=rows_updated,
            rows_appended=rows_appended,
            leases_scanned=leases_scanned,
        )
    )


async def _run_quick(
    cfg: CollectionsSyncConfig,
    buildium: BuildiumClient,
    sheets: GoogleSheetsClient,
    request: SyncRequest,
    request_id: str,
) -> dict:
    """Execute quick sync mode (balance-only updates)."""
    logger.info("Starting quick sync request_id=%s", request_id)

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
    return await run_sync_with_timeout(
        client.fetch_outstanding_balances_for_lease_ids,
        lease_ids,
    )
