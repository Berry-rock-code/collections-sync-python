"""Unit tests for fetch module (concurrent tenant enrichment)."""
import pytest
import asyncio
from unittest.mock import Mock, AsyncMock, MagicMock

from collections_sync.fetch import (
    fetch_active_owed_rows,
    _pick_active_tenant_id,
    _lease_address,
    _first_phone,
)
from core_integrations.buildium import Lease, LeaseTenant, TenantDetails, Unit, Address, PhoneNumber


class TestPickActiveTenantId:
    """Test tenant ID selection logic."""

    def test_no_tenants(self):
        """Test lease with no tenants."""
        lease = Lease(
            id=123,
            lease_to_date=None,
            unit_number=None,
            tenants=[],
            current_tenants=[],
            unit=None,
        )
        assert _pick_active_tenant_id(lease) == 0

    def test_single_inactive_tenant(self):
        """Test lease with single inactive tenant (should return it anyway)."""
        lease = Lease(
            id=123,
            lease_to_date=None,
            unit_number=None,
            tenants=[LeaseTenant(id=456, status="Inactive")],
            current_tenants=[],
            unit=None,
        )
        assert _pick_active_tenant_id(lease) == 456

    def test_single_active_tenant(self):
        """Test lease with single active tenant."""
        lease = Lease(
            id=123,
            lease_to_date=None,
            unit_number=None,
            tenants=[LeaseTenant(id=456, status="Active")],
            current_tenants=[],
            unit=None,
        )
        assert _pick_active_tenant_id(lease) == 456

    def test_multiple_tenants_active_first(self):
        """Test multiple tenants with active tenant first."""
        lease = Lease(
            id=123,
            lease_to_date=None,
            unit_number=None,
            tenants=[
                LeaseTenant(id=456, status="Active"),
                LeaseTenant(id=789, status="Inactive"),
            ],
            current_tenants=[],
            unit=None,
        )
        assert _pick_active_tenant_id(lease) == 456

    def test_multiple_tenants_active_second(self):
        """Test multiple tenants with active tenant second."""
        lease = Lease(
            id=123,
            lease_to_date=None,
            unit_number=None,
            tenants=[
                LeaseTenant(id=456, status="Inactive"),
                LeaseTenant(id=789, status="Active"),
            ],
            current_tenants=[],
            unit=None,
        )
        assert _pick_active_tenant_id(lease) == 789

    def test_no_active_tenants_returns_first(self):
        """Test multiple tenants with no active (returns first)."""
        lease = Lease(
            id=123,
            lease_to_date=None,
            unit_number=None,
            tenants=[
                LeaseTenant(id=456, status="Terminated"),
                LeaseTenant(id=789, status="On Hold"),
            ],
            current_tenants=[],
            unit=None,
        )
        assert _pick_active_tenant_id(lease) == 456

    def test_case_insensitive_active(self):
        """Test that 'Active' status is case-insensitive."""
        lease = Lease(
            id=123,
            lease_to_date=None,
            unit_number=None,
            tenants=[LeaseTenant(id=456, status="ACTIVE")],
            current_tenants=[],
            unit=None,
        )
        assert _pick_active_tenant_id(lease) == 456


class TestLeaseAddress:
    """Test lease address selection."""

    def test_prefer_unit_address(self):
        """Test that unit address is preferred over tenant address."""
        tenant = TenantDetails(
            id=1,
            first_name="John",
            last_name="Doe",
            email="john@test.com",
            address=Address(address_line1="456 Tenant Ave"),
            phone_numbers=[],
        )
        lease = Lease(
            id=123,
            lease_to_date=None,
            unit_number="A1",
            tenants=[],
            current_tenants=[],
            unit=Unit(id=1, property_id=1, address=Address(address_line1="123 Unit St")),
        )

        result = _lease_address(lease, tenant)
        assert result == "123 Unit St"

    def test_fallback_to_tenant_address(self):
        """Test that tenant address is used when unit address missing."""
        tenant = TenantDetails(
            id=1,
            first_name="John",
            last_name="Doe",
            email="john@test.com",
            address=Address(address_line1="456 Tenant Ave"),
            phone_numbers=[],
        )
        lease = Lease(
            id=123,
            lease_to_date=None,
            unit_number="A1",
            tenants=[],
            current_tenants=[],
            unit=Unit(id=1, property_id=1, address=None),
        )

        result = _lease_address(lease, tenant)
        assert result == "456 Tenant Ave"

    def test_no_tenant_no_unit_address(self):
        """Test when both unit and tenant addresses are missing."""
        lease = Lease(
            id=123,
            lease_to_date=None,
            unit_number="A1",
            tenants=[],
            current_tenants=[],
            unit=Unit(id=1, property_id=1, address=None),
        )

        result = _lease_address(lease, None)
        assert result == ""

    def test_none_tenant_uses_unit_address(self):
        """Test that None tenant doesn't break when unit has address."""
        lease = Lease(
            id=123,
            lease_to_date=None,
            unit_number="A1",
            tenants=[],
            current_tenants=[],
            unit=Unit(id=1, property_id=1, address=Address(address_line1="123 Unit St")),
        )

        result = _lease_address(lease, None)
        assert result == "123 Unit St"

    def test_empty_string_unit_address_falls_back(self):
        """Test that empty string unit address falls back to tenant."""
        tenant = TenantDetails(
            id=1,
            first_name="John",
            last_name="Doe",
            email="john@test.com",
            address=Address(address_line1="456 Tenant Ave"),
            phone_numbers=[],
        )
        lease = Lease(
            id=123,
            lease_to_date=None,
            unit_number="A1",
            tenants=[],
            current_tenants=[],
            unit=Unit(id=1, property_id=1, address=Address(address_line1="")),
        )

        result = _lease_address(lease, tenant)
        assert result == "456 Tenant Ave"


class TestFirstPhone:
    """Test phone number extraction."""

    def test_no_phones(self):
        """Test tenant with no phone numbers."""
        tenant = TenantDetails(
            id=1,
            first_name="John",
            last_name="Doe",
            email="john@test.com",
            address=None,
            phone_numbers=[],
        )

        result = _first_phone(tenant)
        assert result == ""

    def test_single_phone(self):
        """Test tenant with single phone."""
        tenant = TenantDetails(
            id=1,
            first_name="John",
            last_name="Doe",
            email="john@test.com",
            address=None,
            phone_numbers=[PhoneNumber(number="555-1234")],
        )

        result = _first_phone(tenant)
        assert result == "555-1234"

    def test_multiple_phones_returns_first(self):
        """Test that first phone is returned when multiple."""
        tenant = TenantDetails(
            id=1,
            first_name="John",
            last_name="Doe",
            email="john@test.com",
            address=None,
            phone_numbers=[
                PhoneNumber(number="555-1234"),
                PhoneNumber(number="555-5678"),
            ],
        )

        result = _first_phone(tenant)
        assert result == "555-1234"


@pytest.mark.asyncio
class TestFetchActiveOwedRows:
    """Test async concurrent tenant enrichment."""

    async def test_empty_response(self):
        """Test with no leases or balances."""
        mock_client = Mock()
        mock_client.fetch_outstanding_balances = Mock(return_value={})
        mock_client.list_all_leases = Mock(return_value=[])

        rows, leases_scanned = await fetch_active_owed_rows(
            client=mock_client,
            max_pages=0,
            max_rows=0,
            existing_lease_ids=set(),
        )

        assert rows == []
        assert leases_scanned == 0

    async def test_single_lease_with_balance(self):
        """Test with single lease that has balance."""
        mock_client = Mock()
        mock_client.fetch_outstanding_balances = Mock(return_value={123: 500.0})
        mock_client.list_all_leases = Mock(
            return_value=[
                Lease(
                    id=123,
                    lease_to_date=None,
                    unit_number="A1",
                    tenants=[LeaseTenant(id=456, status="Active")],
                    current_tenants=[],
                    unit=Unit(
                        id=1,
                        property_id=1,
                        address=Address(address_line1="123 Main St"),
                    ),
                )
            ]
        )
        mock_client.get_tenant_details = Mock(
            return_value=TenantDetails(
                id=456,
                first_name="John",
                last_name="Doe",
                email="john@test.com",
                address=None,
                phone_numbers=[PhoneNumber(number="555-1234")],
            )
        )

        rows, leases_scanned = await fetch_active_owed_rows(
            client=mock_client,
            max_pages=0,
            max_rows=0,
            existing_lease_ids=set(),
        )

        assert len(rows) == 1
        assert rows[0].lease_id == 123
        assert rows[0].amount_owed == 500.0
        assert rows[0].name == "John Doe"
        assert rows[0].phone == "555-1234"
        assert leases_scanned == 1

    async def test_skip_zero_balance_non_existing(self):
        """Test that leases with zero balance and not existing are skipped."""
        mock_client = Mock()
        mock_client.fetch_outstanding_balances = Mock(return_value={})
        mock_client.list_all_leases = Mock(
            return_value=[
                Lease(
                    id=123,
                    lease_to_date=None,
                    unit_number="A1",
                    tenants=[LeaseTenant(id=456, status="Active")],
                    current_tenants=[],
                    unit=Unit(
                        id=1,
                        property_id=1,
                        address=Address(address_line1="123 Main St"),
                    ),
                )
            ]
        )

        rows, leases_scanned = await fetch_active_owed_rows(
            client=mock_client,
            max_pages=0,
            max_rows=0,
            existing_lease_ids=set(),  # Lease 123 not in existing
        )

        assert len(rows) == 0
        assert leases_scanned == 1

    async def test_keep_existing_zero_balance(self):
        """Test that existing leases with zero balance are kept."""
        mock_client = Mock()
        mock_client.fetch_outstanding_balances = Mock(return_value={})
        mock_client.list_all_leases = Mock(
            return_value=[
                Lease(
                    id=123,
                    lease_to_date=None,
                    unit_number="A1",
                    tenants=[LeaseTenant(id=456, status="Active")],
                    current_tenants=[],
                    unit=Unit(
                        id=1,
                        property_id=1,
                        address=Address(address_line1="123 Main St"),
                    ),
                )
            ]
        )
        mock_client.get_tenant_details = Mock(
            return_value=TenantDetails(
                id=456,
                first_name="John",
                last_name="Doe",
                email="john@test.com",
                address=None,
                phone_numbers=[],
            )
        )

        rows, leases_scanned = await fetch_active_owed_rows(
            client=mock_client,
            max_pages=0,
            max_rows=0,
            existing_lease_ids={123},  # Lease 123 exists in sheet
        )

        assert len(rows) == 1
        assert rows[0].lease_id == 123
        assert rows[0].amount_owed == 0.0

    async def test_sorted_by_amount_descending(self):
        """Test that results are sorted by amount owed descending."""
        mock_client = Mock()
        mock_client.fetch_outstanding_balances = Mock(
            return_value={123: 500.0, 456: 1500.0, 789: 250.0}
        )
        mock_client.list_all_leases = Mock(
            return_value=[
                Lease(
                    id=123,
                    lease_to_date=None,
                    unit_number="A1",
                    tenants=[LeaseTenant(id=1, status="Active")],
                    current_tenants=[],
                    unit=Unit(id=1, property_id=1, address=None),
                ),
                Lease(
                    id=456,
                    lease_to_date=None,
                    unit_number="A2",
                    tenants=[LeaseTenant(id=2, status="Active")],
                    current_tenants=[],
                    unit=Unit(id=2, property_id=1, address=None),
                ),
                Lease(
                    id=789,
                    lease_to_date=None,
                    unit_number="A3",
                    tenants=[LeaseTenant(id=3, status="Active")],
                    current_tenants=[],
                    unit=Unit(id=3, property_id=1, address=None),
                ),
            ]
        )
        mock_client.get_tenant_details = Mock(
            return_value=TenantDetails(
                id=1,
                first_name="Test",
                last_name="Tenant",
                email="test@test.com",
                address=None,
                phone_numbers=[],
            )
        )

        rows, _ = await fetch_active_owed_rows(
            client=mock_client,
            max_pages=0,
            max_rows=0,
            existing_lease_ids=set(),
        )

        # Should be sorted descending
        assert rows[0].amount_owed == 1500.0
        assert rows[1].amount_owed == 500.0
        assert rows[2].amount_owed == 250.0

    async def test_max_rows_cap(self):
        """Test that max_rows limits results."""
        mock_client = Mock()
        mock_client.fetch_outstanding_balances = Mock(
            return_value={1: 100.0, 2: 200.0, 3: 300.0}
        )
        mock_client.list_all_leases = Mock(
            return_value=[
                Lease(
                    id=i,
                    lease_to_date=None,
                    unit_number=f"A{i}",
                    tenants=[LeaseTenant(id=i, status="Active")],
                    current_tenants=[],
                    unit=Unit(id=i, property_id=1, address=None),
                )
                for i in range(1, 4)
            ]
        )
        mock_client.get_tenant_details = Mock(
            return_value=TenantDetails(
                id=1,
                first_name="Test",
                last_name="Tenant",
                email="test@test.com",
                address=None,
                phone_numbers=[],
            )
        )

        rows, _ = await fetch_active_owed_rows(
            client=mock_client,
            max_pages=0,
            max_rows=2,  # Limit to 2 rows
            existing_lease_ids=set(),
        )

        assert len(rows) == 2

    async def test_tenant_lookup_failure_creates_placeholder(self):
        """Test that failed tenant lookup creates placeholder row."""
        mock_client = Mock()
        mock_client.fetch_outstanding_balances = Mock(return_value={123: 500.0})
        mock_client.list_all_leases = Mock(
            return_value=[
                Lease(
                    id=123,
                    lease_to_date=None,
                    unit_number="A1",
                    tenants=[LeaseTenant(id=456, status="Active")],
                    current_tenants=[],
                    unit=Unit(
                        id=1,
                        property_id=1,
                        address=Address(address_line1="123 Main St"),
                    ),
                )
            ]
        )
        mock_client.get_tenant_details = Mock(
            side_effect=Exception("API error")
        )

        rows, _ = await fetch_active_owed_rows(
            client=mock_client,
            max_pages=0,
            max_rows=0,
            existing_lease_ids=set(),
        )

        assert len(rows) == 1
        assert rows[0].lease_id == 123
        assert rows[0].name == "(tenant lookup failed)"
