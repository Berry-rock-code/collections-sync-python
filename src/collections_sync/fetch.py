"""Async concurrent tenant enrichment for bulk sync."""
import asyncio
import logging
from datetime import datetime

from core_integrations.buildium import BuildiumClient, Lease, TenantDetails

from .models import DelinquentRow

logger = logging.getLogger(__name__)


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

    Args:
        client: Initialized BuildiumClient.
        max_pages: Max pages to fetch (0 = no cap).
        max_rows: Max rows to return (0 = no cap).
        bal_timeout: Timeout for balance fetch (seconds).
        lease_timeout: Timeout for lease fetch (seconds).
        tenant_timeout: Timeout per tenant lookup (seconds).
        tenant_sleep_ms: Sleep before each tenant API call (ms).
        existing_lease_ids: Set of lease IDs already in the sheet.

    Returns:
        Tuple of (list of DelinquentRow, total leases scanned).
    """
    if existing_lease_ids is None:
        existing_lease_ids = set()

    # Step A: Fetch outstanding balances
    logger.info("Fetching outstanding balances...")
    debt_map: dict[int, float] = await asyncio.to_thread(
        client.fetch_outstanding_balances
    )
    logger.info("Found %d leases with outstanding balances", len(debt_map))

    # Step B: Fetch all leases
    logger.info("Fetching leases...")
    leases: list[Lease] = await asyncio.to_thread(
        lambda: (
            client.list_all_leases()
            if max_pages == 0
            else client.list_all_leases_limited(max_pages)
        )
    )
    logger.info("Fetched %d total leases", len(leases))

    # Step C: Concurrent tenant enrichment with 3-worker semaphore
    sem = asyncio.Semaphore(3)
    tenant_cache: dict[int, TenantDetails] = {}
    cache_lock = asyncio.Lock()
    results: list[DelinquentRow] = []
    results_lock = asyncio.Lock()

    today = datetime.now().strftime("%m/%d/%Y")

    async def enrich_lease(lease: Lease, owed: float) -> None:
        """Enrich a lease with tenant details."""
        async with sem:
            # Check if we've hit the max rows limit
            async with results_lock:
                if max_rows > 0 and len(results) >= max_rows:
                    return

            # Pick a tenant to look up
            tenant_id = _pick_active_tenant_id(lease)
            addr = _lease_address(lease, None)

            if tenant_id == 0:
                async with results_lock:
                    results.append(
                        DelinquentRow(
                            lease_id=lease.id,
                            name="(no active tenant found)",
                            address=addr,
                            phone="",
                            email="",
                            amount_owed=owed,
                            date_added=today,
                        )
                    )
                return

            # Check cache first
            async with cache_lock:
                cached = tenant_cache.get(tenant_id)

            if cached is None:
                # Not in cache, fetch it
                await asyncio.sleep(tenant_sleep_ms / 1000.0)

                try:
                    td = await asyncio.to_thread(
                        client.get_tenant_details, tenant_id
                    )
                except Exception as e:
                    logger.warning(
                        "tenant lookup failed leaseID=%d tenantID=%d: %s",
                        lease.id,
                        tenant_id,
                        e,
                    )
                    async with results_lock:
                        results.append(
                            DelinquentRow(
                                lease_id=lease.id,
                                name="(tenant lookup failed)",
                                address=addr,
                                phone="",
                                email="",
                                amount_owed=owed,
                                date_added=today,
                            )
                        )
                    return

                # Store in cache
                async with cache_lock:
                    tenant_cache[tenant_id] = td
            else:
                td = cached

            # Get the best address with tenant data
            addr = _lease_address(lease, td)

            # Append the enriched row
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

    # Wait for all tasks to complete
    await asyncio.gather(*tasks)

    # Sort by amount owed descending
    results.sort(key=lambda r: r.amount_owed, reverse=True)

    # Final trim in case tasks raced past max_rows
    if max_rows > 0 and len(results) > max_rows:
        results = results[:max_rows]

    return results, len(leases)


def _pick_active_tenant_id(lease: Lease) -> int:
    """Pick the first active tenant ID from a lease.

    Falls back to the first tenant if none are explicitly active.
    Returns 0 if no tenants.
    """
    if not lease.tenants:
        logger.debug("lease %d has no tenants", lease.id)
        return 0

    for t in lease.tenants:
        if t.status.lower() == "active":
            logger.debug("lease %d found active tenant %d", lease.id, t.id)
            return t.id

    logger.debug("lease %d has %d tenants but none active, using first", lease.id, len(lease.tenants))
    return lease.tenants[0].id


def _lease_address(lease: Lease, td: TenantDetails | None) -> str:
    """Get the best available address for a lease.

    Prefers unit address over tenant address.
    """
    if lease.unit and lease.unit.address and lease.unit.address.address_line1:
        return lease.unit.address.address_line1

    if td and td.address and td.address.address_line1:
        return td.address.address_line1

    return ""


def _first_phone(td: TenantDetails) -> str:
    """Get the first phone number from tenant details."""
    if td.phone_numbers:
        return td.phone_numbers[0].number
    return ""
