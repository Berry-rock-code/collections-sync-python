"""Client library for calling collections-sync service.

Usage:
    from collections_sync_client import CollectionsSyncClient

    client = CollectionsSyncClient("https://your-service-url.run.app")
    result = await client.quick_sync()
"""
import httpx
import logging
from typing import Optional, Literal

logger = logging.getLogger(__name__)


class SyncResult:
    """Result of a sync operation."""

    def __init__(self, data: dict):
        self.mode = data.get("mode")
        self.rows_prepared = data.get("rows_prepared", 0)
        self.rows_updated = data.get("rows_updated", 0)
        self.rows_appended = data.get("rows_appended", 0)
        self.existing_keys = data.get("existing_keys", 0)
        self.leases_scanned = data.get("leases_scanned", 0)

    def __repr__(self):
        return (
            f"SyncResult(mode={self.mode}, "
            f"rows_updated={self.rows_updated}, "
            f"rows_appended={self.rows_appended}, "
            f"leases_scanned={self.leases_scanned})"
        )


class CollectionsSyncClient:
    """Client for calling the collections-sync service.

    Handles HTTP communication with the remote service, retries, and
    error handling for service-to-service calls.
    """

    def __init__(
        self,
        service_url: str,
        timeout: float = 600.0,
        retries: int = 1,
    ):
        """Initialize client.

        Args:
            service_url: URL of collections-sync service
                (e.g., "https://collections-sync-abc123.run.app")
            timeout: Request timeout in seconds (default: 600s for bulk syncs)
            retries: Number of retries on transient failures (default: 1)
        """
        self.service_url = service_url.rstrip("/")
        self.timeout = timeout
        self.retries = retries
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create async HTTP client."""
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.timeout)
        return self._client

    async def health_check(self) -> bool:
        """Check if service is healthy.

        Returns:
            True if service is up and responding, False otherwise.
        """
        try:
            client = await self._get_client()
            response = await client.get(f"{self.service_url}/")
            return response.status_code == 200
        except Exception as e:
            logger.warning(f"Health check failed: {e}")
            return False

    async def quick_sync(
        self,
        max_rows: int = 10,
    ) -> SyncResult:
        """Trigger quick sync (balance updates only).

        Quick sync only updates existing rows with latest balances from Buildium.
        Fast (usually < 1 minute) and good for frequent updates.

        Args:
            max_rows: Maximum rows to update (0 = no limit)

        Returns:
            SyncResult with operation details

        Raises:
            httpx.HTTPError: If request fails
        """
        return await self._trigger_sync(
            mode="quick",
            max_pages=0,
            max_rows=max_rows,
        )

    async def bulk_sync(
        self,
        max_pages: int = 0,
        max_rows: int = 0,
    ) -> SyncResult:
        """Trigger bulk sync (full rescan).

        Bulk sync fetches all delinquent leases from Buildium and updates
        the Google Sheet, adding new rows and updating existing ones.
        Can take 5-30 minutes depending on data volume.

        Args:
            max_pages: Maximum pages to fetch (0 = all)
            max_rows: Maximum rows to process (0 = all)

        Returns:
            SyncResult with operation details

        Raises:
            httpx.HTTPError: If request fails
        """
        return await self._trigger_sync(
            mode="bulk",
            max_pages=max_pages,
            max_rows=max_rows,
        )

    async def _trigger_sync(
        self,
        mode: Literal["quick", "bulk"],
        max_pages: int,
        max_rows: int,
    ) -> SyncResult:
        """Internal method to trigger sync.

        Args:
            mode: Sync mode ("quick" or "bulk")
            max_pages: Maximum pages to fetch
            max_rows: Maximum rows to process

        Returns:
            SyncResult with operation details

        Raises:
            httpx.HTTPError: If request fails
        """
        client = await self._get_client()
        payload = {
            "mode": mode,
            "max_pages": max_pages,
            "max_rows": max_rows,
        }

        last_error = None
        for attempt in range(self.retries + 1):
            try:
                logger.info(
                    f"Triggering {mode} sync (attempt {attempt + 1}/{self.retries + 1})"
                )
                response = await client.post(
                    f"{self.service_url}/",
                    json=payload,
                )
                response.raise_for_status()
                result = SyncResult(response.json())
                logger.info(f"Sync completed: {result}")
                return result
            except httpx.HTTPError as e:
                last_error = e
                logger.warning(f"Sync attempt {attempt + 1} failed: {e}")
                if attempt < self.retries:
                    logger.info(f"Retrying in {2 ** attempt}s...")
                    import asyncio
                    await asyncio.sleep(2 ** attempt)

        raise last_error or Exception("Unknown error during sync")

    async def close(self):
        """Close the HTTP client."""
        if self._client:
            await self._client.aclose()

    async def __aenter__(self):
        """Async context manager entry."""
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self.close()


# Example usage
if __name__ == "__main__":
    import asyncio

    async def main():
        # Example 1: Using context manager (recommended)
        async with CollectionsSyncClient("https://your-service-url.run.app") as client:
            # Check health
            if await client.health_check():
                print("✅ Service is healthy")

                # Run quick sync
                result = await client.quick_sync()
                print(f"Quick sync result: {result}")

                # Run bulk sync
                # result = await client.bulk_sync()
                # print(f"Bulk sync result: {result}")
            else:
                print("❌ Service is not responding")

        # Example 2: Manual client management
        client = CollectionsSyncClient("https://your-service-url.run.app")
        try:
            result = await client.quick_sync()
            print(f"Result: {result}")
        finally:
            await client.close()

    asyncio.run(main())
