import pytest
from datetime import datetime, timezone
from services.intake_tasks import _resolve_organization_from_sender, _find_applicable_playbook, _auto_assign_intake

class TestIntakeTasks:
    """Test suite for intake processing functions."""

    @pytest.mark.asyncio
    async def test_resolve_org_from_sender_domain(self, db):
        """Test organization resolution by email domain."""
        # Setup: create org with domain
        org_id = "test-org-1"
        await db.organizations.insert_one({
            "id": org_id,
            "name": "Test Org",
            "domains": ["vendor.com"],
            "createdAt": datetime.now(timezone.utc).isoformat(),
            "updatedAt": datetime.now(timezone.utc).isoformat(),
        })
        
        result = await _resolve_organization_from_sender("user@vendor.com", db)
        assert result == org_id

    @pytest.mark.asyncio
    async def test_resolve_org_from_sender_user_email(self, db):
        """Test organization resolution by user email fallback."""
        org_id = "test-org-2"
        user_id = "test-user-1"
        await db.organizations.insert_one({
            "id": org_id,
            "name": "Test Org 2",
            "domains": [],
            "createdAt": datetime.now(timezone.utc).isoformat(),
            "updatedAt": datetime.now(timezone.utc).isoformat(),
        })
        await db.users.insert_one({
            "id": user_id,
            "organizationId": org_id,
            "email": "buyer@company.com",
            "role": "user",
            "createdAt": datetime.now(timezone.utc).isoformat(),
        })
        
        result = await _resolve_organization_from_sender("buyer@company.com", db)
        assert result == org_id

    @pytest.mark.asyncio
    async def test_resolve_org_unknown_sender(self, db):
        """Test organization resolution returns None for unknown sender."""
        result = await _resolve_organization_from_sender("unknown@nowhere.com", db)
        assert result is None

    @pytest.mark.asyncio
    async def test_find_applicable_playbook_by_type(self, db):
        """Test finding playbook by contract type."""
        org_id = "test-org-3"
        playbook_id = "pb-1"
        await db.legal_playbooks.insert_one({
            "id": playbook_id,
            "organizationId": org_id,
            "name": "NDA Playbook",
            "contract_types": ["NDA"],
            "is_active": True,
            "is_default": False,
            "rules": [],
            "createdAt": datetime.now(timezone.utc).isoformat(),
        })
        
        result = await _find_applicable_playbook(org_id, "NDA", None, db)
        assert result == playbook_id

    @pytest.mark.asyncio
    async def test_find_applicable_playbook_default(self, db):
        """Test finding default playbook when no type match."""
        org_id = "test-org-4"
        default_pb = "pb-default"
        await db.legal_playbooks.insert_one({
            "id": default_pb,
            "organizationId": org_id,
            "name": "Default Playbook",
            "contract_types": [],
            "is_active": True,
            "is_default": True,
            "rules": [],
            "createdAt": datetime.now(timezone.utc).isoformat(),
        })
        
        result = await _find_applicable_playbook(org_id, "UnknownType", None, db)
        assert result == default_pb

    @pytest.mark.asyncio
    async def test_find_applicable_playbook_none(self, db):
        """Test finding playbook returns None when no playbooks exist."""
        result = await _find_applicable_playbook("nonexistent-org", "NDA", None, db)
        assert result is None

    @pytest.mark.asyncio
    async def test_auto_assign_intake(self, db):
        """Test auto-assignment to admin/manager."""
        org_id = "test-org-5"
        admin_id = "admin-1"
        manager_id = "manager-1"
        await db.users.insert_one({
            "id": admin_id,
            "organizationId": org_id,
            "email": "admin@test.com",
            "role": "admin",
            "isActive": True,
            "createdAt": datetime.now(timezone.utc).isoformat(),
        })
        await db.users.insert_one({
            "id": manager_id,
            "organizationId": org_id,
            "email": "manager@test.com",
            "role": "manager",
            "isActive": True,
            "createdAt": datetime.now(timezone.utc).isoformat(),
        })
        
        result = await _auto_assign_intake(org_id, {}, db)
        assert result in [admin_id, manager_id]

    @pytest.mark.asyncio
    async def test_auto_assign_intake_no_users(self, db):
        """Test auto-assignment returns None when no eligible users."""
        result = await _auto_assign_intake("empty-org", {}, db)
        assert result is None