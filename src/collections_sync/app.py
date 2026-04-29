"""FastAPI application for collections-sync service."""
import json
import logging
import os
import sys
import tempfile
import traceback
import uuid
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import datetime

from fastapi import FastAPI, HTTPException, Query
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


def _error_response(
    error_type: str,
    request_id: str,
    message: str,
    status_code: int,
    debug: bool = False,
    exception: Exception = None,
    user_actions: list[str] = None,
    technical_info: dict = None,
) -> dict:
    """Format error response based on debug mode.

    Args:
        error_type: Error type (e.g., "DataCorruptionError")
        request_id: Request ID for tracing
        message: Error message
        status_code: HTTP status code
        debug: If True, include full technical details and stack trace
        exception: The original exception (for stack trace)
        user_actions: List of actions for non-technical users
        technical_info: Dict of technical details

    Returns:
        Error detail dict for HTTPException
    """
    if debug:
        # Full technical response
        response = {
            "error_type": error_type,
            "request_id": request_id,
            "http_status": status_code,
            "message": message,
            "exception_type": type(exception).__name__ if exception else None,
            "stack_trace": traceback.format_exc() if exception else None,
            "technical_info": technical_info or {},
        }
    else:
        # User-friendly response
        response = {
            "error_type": error_type,
            "request_id": request_id,
            "message": message,
            "actions": user_actions or ["Contact support with request_id"],
        }
        if technical_info:
            response["technical_info"] = technical_info

    return response


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
async def trigger_sync(request: SyncRequest, debug: bool = Query(False)) -> dict:
    """Trigger synchronization.

    Args:
        request: SyncRequest with mode, max_pages, max_rows.
        debug: If True, return full technical error details with stack traces.

    Returns:
        SyncResult as JSON with request_id for tracing.

    Usage:
        POST / ?mode=bulk               → User-friendly errors
        POST / ?mode=bulk&debug=true    → Full technical errors with stack traces
    """
    cfg = app.state.cfg
    buildium = app.state.buildium
    sheets = app.state.sheets

    request_id = str(uuid.uuid4())
    logger.info("Sync requested request_id=%s mode=%s debug=%s", request_id, request.mode, debug)

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
            detail=_error_response(
                error_type="LockTimeoutError",
                request_id=request_id,
                message=str(e),
                status_code=503,
                debug=debug,
                exception=e,
                user_actions=[
                    "1. Wait 30-60 seconds and retry",
                    "2. If persistent, contact support with request_id",
                ],
                technical_info={
                    "reason": "Another sync is currently in progress (lock held for > 30 seconds)",
                    "lock_sheet": cfg.sync_lock_sheet,
                    "lock_timeout_seconds": cfg.sync_lock_timeout_seconds,
                    "lock_stale_seconds": cfg.sync_lock_stale_seconds,
                    "spreadsheet_id": cfg.effective_sheet_id,
                },
            ),
        )
    except DataValidationError as e:
        logger.error("Data validation error request_id=%s: %s", request_id, e)
        raise HTTPException(
            status_code=422,
            detail=_error_response(
                error_type="DataValidationError",
                request_id=request_id,
                message=str(e),
                status_code=422,
                debug=debug,
                exception=e,
                user_actions=[
                    "1. Some rows from Buildium have data quality issues",
                    "2. Invalid rows are automatically filtered out",
                    "3. Contact DevOps if this happens frequently",
                ],
                technical_info={
                    "reason": "Some rows failed validation (negative amounts, invalid dates, etc.)",
                    "validation_error_details": str(e),
                    "note": "Invalid rows are filtered out. Sync continues with valid data only.",
                    "common_issues": [
                        "lease_id <= 0",
                        "amount_owed < 0",
                        "empty name field",
                        "date_added not matching MM/DD/YYYY",
                    ],
                },
            ),
        )
    except DataCorruptionError as e:
        logger.error(
            "Data corruption detected request_id=%s: %s",
            request_id,
            e,
            exc_info=True,
        )
        sheet_url = f"https://docs.google.com/spreadsheets/d/{cfg.effective_sheet_id}/edit"
        raise HTTPException(
            status_code=500,
            detail=_error_response(
                error_type="DataCorruptionError",
                request_id=request_id,
                message=str(e),
                status_code=500,
                debug=debug,
                exception=e,
                user_actions=[
                    f"1. Open sheet: {sheet_url}",
                    f"2. Check tab '{cfg.worksheet_name}' for incomplete rows",
                    "3. Look for rows with missing data in any columns",
                    "4. Save a backup (File → Version history)",
                    "5. Manually fix incomplete rows",
                    f"6. Contact support with request_id={request_id}",
                ],
                technical_info={
                    "sheet_id": cfg.effective_sheet_id,
                    "worksheet": cfg.worksheet_name,
                    "error_message": str(e),
                    "cause": "Post-write checksum verification failed - expected != actual",
                    "severity": "CRITICAL - requires manual intervention",
                    "what_happened": [
                        "1. Rows were written to Google Sheets",
                        "2. Service read back the written data",
                        "3. Checksum comparison failed (data was corrupted or modified)",
                        "4. Sheet may be partially written or have incorrect values",
                    ],
                    "notes": [
                        "NO RETRY performed (sheet state unknown)",
                        "Manual inspection required before retry",
                        "Check Google Sheets Activity log for any concurrent writes",
                        "Verify Buildium API and Google Sheets quota are healthy",
                    ],
                    "docs": "See docs/ROBUSTNESS_FEATURES.md#step-9-verify-post-write-state",
                },
            ),
        )
    except Exception as e:
        logger.error("Unexpected error request_id=%s: %s", request_id, e, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=_error_response(
                error_type="InternalError",
                request_id=request_id,
                message=str(e),
                status_code=500,
                debug=debug,
                exception=e,
                user_actions=[
                    f"1. Note this request_id: {request_id}",
                    "2. Check if Buildium or Google Sheets are down",
                    "3. Try again in 30 seconds",
                    "4. If problem persists, contact DevOps",
                ],
                technical_info={
                    "exception_type": type(e).__name__,
                    "error_message": str(e),
                    "spreadsheet_id": cfg.effective_sheet_id,
                    "worksheet": cfg.worksheet_name,
                    "troubleshooting": [
                        "1. Check Buildium API status",
                        "2. Check Google Sheets quota and rate limits",
                        "3. Check service credentials (GCP IAM, API keys)",
                        "4. Review timeout settings (BAL_TIMEOUT, LEASE_TIMEOUT, TENANT_TIMEOUT)",
                        "5. Check network connectivity to both APIs",
                    ],
                },
            ),
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

    # Initialize lock manager and validator (used by both paths)
    lock_mgr = SyncLockManager(
        client=sheets,
        spreadsheet_id=cfg.effective_sheet_id,
        lock_sheet=cfg.sync_lock_sheet,
        acquire_timeout=float(cfg.sync_lock_timeout_seconds),
        stale_timeout=float(cfg.sync_lock_stale_seconds),
    )
    validator = DataValidator()

    # Upsert to sheet
    if cfg.sync_enable_atomic:
        logger.info(
            "Using atomic upsert with locking request_id=%s", request_id
        )

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

        # Even without atomic verification, use locking to prevent concurrent conflicts
        with lock_mgr:
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
