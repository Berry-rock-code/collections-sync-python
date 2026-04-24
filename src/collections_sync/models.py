"""Core data models for collections sync."""
from dataclasses import dataclass
from enum import Enum
from typing import Any

from pydantic import BaseModel


class SyncMode(str, Enum):
    """Synchronization mode."""
    BULK = "bulk"
    QUICK = "quick"


@dataclass
class DelinquentRow:
    """A delinquent tenant row to sync to the sheet."""
    lease_id: int
    name: str
    address: str
    phone: str
    email: str
    amount_owed: float
    date_added: str  # MM/DD/YYYY


@dataclass
class SyncResult:
    """Result of a sync operation."""
    mode: str
    existing_keys: int = 0
    rows_prepared: int = 0
    rows_updated: int = 0
    rows_appended: int = 0
    leases_scanned: int = 0


class SyncRequest(BaseModel):
    """HTTP request body for triggering sync."""
    mode: SyncMode = SyncMode.BULK
    max_pages: int = 0
    max_rows: int = 0
