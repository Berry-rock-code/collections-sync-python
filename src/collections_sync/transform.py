"""Column definitions and row transformation for collections sync."""
from datetime import datetime
from typing import Any

from .models import DelinquentRow

KEY_HEADER = "Lease ID"

HEADERS: list[str] = [
    "Date First Added",
    "Name",
    "Address:",
    "Phone Number",
    "Email",
    "Amount Owed:",
    "Date of 5 Day:",
    "Expired Lease",
    "Returned Payment",
    "Date of Next Payment",
    "Date of Last payment",
    "Payment Plan Details",
    "Missed Payment Plan and not Rescheduled",
    "Remarks:",
    "Last Edited Date",
    "Status",
    "CALL 1",
    "CALL 2",
    "CALL 3",
    "CALL 4",
    "CALL 5",
    "Last Call Date",
    "Eviction Filed Date",
    "Eviction Court Date",
    "Lease ID",
    "Phone Number",
    "Date Status Changed to Eviction",
]

OWNED_HEADERS: set[str] = {
    "Date First Added",
    "Name",
    "Address:",
    "Phone Number",
    "Email",
    "Amount Owed:",
    "Lease ID",
    "Last Edited Date",
}


def to_sheet_values(rows: list[DelinquentRow]) -> list[list[Any]]:
    """Convert a list of DelinquentRow to sheet row values.

    Each row is expanded to match the full HEADERS layout, with owned columns
    filled in and other columns left empty.

    Args:
        rows: List of DelinquentRow objects.

    Returns:
        List of rows, where each row is a list of values matching HEADERS.
    """
    # Build a map of header name (normalized) -> index
    header_indices: dict[str, int] = {}
    for i, header in enumerate(HEADERS):
        normalized = header.strip().lower()
        if normalized not in header_indices:
            header_indices[normalized] = i

    out = []
    now = datetime.now().strftime("%m/%d/%Y")

    for r in rows:
        row: list[Any] = [None] * len(HEADERS)

        def set_value(header_name: str, value: Any) -> None:
            """Set a value at the column matching header_name."""
            normalized = header_name.strip().lower()
            idx = header_indices.get(normalized)
            if idx is not None and idx < len(row):
                row[idx] = value

        set_value("Date First Added", r.date_added)
        set_value("Name", r.name)
        set_value("Address:", r.address)
        set_value("Phone Number", r.phone)
        set_value("Email", r.email)
        set_value("Amount Owed:", r.amount_owed)
        set_value("Last Edited Date", now)
        set_value("Lease ID", r.lease_id)

        # Convert None to empty string for output
        row = [v if v is not None else "" for v in row]
        out.append(row)

    return out
