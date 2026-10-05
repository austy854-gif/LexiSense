"""Canonical MongoDB index specification for LexiSense.

Kept in one declarative place (instead of scattered ``create_index`` calls in
``server.py``) so that:

* every query path can be traced to the index that serves it,
* the unit tests can assert index coverage without a live MongoDB, and
* a new collection cannot ship without an explicit indexing decision.

Index rationale (id -> organisation scoping -> hot sort/filter field):

===================  ==========================================================
Collection           Serves
===================  ==========================================================
users                login by email (unique); org member lists; documents are
                     also looked up by the application ``id`` field (unique).
contracts            the hottest collection: every list/dashboard query is
                     scoped by ``organizationId`` and then sorted/filtered by
                     ``createdAt``/``expiryDate``/``status``/``riskLevel``/
                     ``contractType``/``uploadedBy``. The application ``id``
                     lookup (unique) backs every by-id endpoint.
contract_versions    version history per contract.
invitations          token lookup on acceptance + per-org listing.
expiration_alerts    the alert scheduler looks alerts up per contract and the
                     alerts screen lists them per organisation.
templates            per-org template listing.
audit_logs           per-org audit listing filtered by resource/action.
notifications        per-user inbox and unread counter.
chat_history         per-contract transcript; cleaned up on contract delete.
organization_billing the trial/quota record (one per org) and the Stripe
                     webhook lookup by customer id.
===================  ==========================================================
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

#: ``{collection: [index_spec, ...]}``. ``keys`` is a pymongo key list.
INDEX_SPECIFICATION: Dict[str, List[Dict[str, Any]]] = {
    "users": [
        {"keys": [("email", 1)], "unique": True, "name": "uq_users_email"},
        {"keys": [("id", 1)], "unique": True, "name": "uq_users_id"},
        {"keys": [("organizationId", 1), ("createdAt", -1)], "name": "ix_users_org_created"},
    ],
    "organizations": [
        {"keys": [("id", 1)], "unique": True, "name": "uq_organizations_id"},
    ],
    "contracts": [
        {"keys": [("id", 1)], "unique": True, "name": "uq_contracts_id"},
        {"keys": [("organizationId", 1), ("createdAt", -1)], "name": "ix_contracts_org_created"},
        {"keys": [("organizationId", 1), ("expiryDate", 1)], "name": "ix_contracts_org_expiry"},
        {"keys": [("organizationId", 1), ("status", 1)], "name": "ix_contracts_org_status"},
        {"keys": [("organizationId", 1), ("riskLevel", 1)], "name": "ix_contracts_org_risk"},
        {"keys": [("organizationId", 1), ("contractType", 1)], "name": "ix_contracts_org_type"},
        {"keys": [("organizationId", 1), ("uploadedBy", 1)], "name": "ix_contracts_org_uploader"},
    ],
    "contract_versions": [
        {"keys": [("contractId", 1), ("version", -1)], "name": "ix_versions_contract_version"},
    ],
    "invitations": [
        {"keys": [("token", 1)], "unique": True, "name": "uq_invitations_token"},
        {"keys": [("organizationId", 1), ("email", 1)], "name": "ix_invitations_org_email"},
        {"keys": [("organizationId", 1), ("createdAt", -1)], "name": "ix_invitations_org_created"},
    ],
    "expiration_alerts": [
        {"keys": [("contractId", 1), ("daysBeforeExpiry", 1)], "name": "ix_alerts_contract_days"},
        {"keys": [("organizationId", 1), ("createdAt", -1)], "name": "ix_alerts_org_created"},
    ],
    "templates": [
        {"keys": [("organizationId", 1), ("name", 1)], "name": "ix_templates_org_name"},
        {"keys": [("organizationId", 1), ("createdAt", -1)], "name": "ix_templates_org_created"},
    ],
    "audit_logs": [
        {"keys": [("organizationId", 1), ("createdAt", -1)], "name": "ix_audit_org_created"},
        {"keys": [("organizationId", 1), ("resourceType", 1)], "name": "ix_audit_org_resource"},
        {"keys": [("organizationId", 1), ("action", 1)], "name": "ix_audit_org_action"},
    ],
    "notifications": [
        {"keys": [("userId", 1), ("createdAt", -1)], "name": "ix_notifications_user_created"},
        {"keys": [("userId", 1), ("isRead", 1)], "name": "ix_notifications_user_read"},
    ],
    "chat_history": [
        {"keys": [("contractId", 1), ("createdAt", -1)], "name": "ix_chat_contract_created"},
    ],
    "organization_billing": [
        {"keys": [("organizationId", 1)], "unique": True, "name": "uq_billing_org"},
        {"keys": [("stripeCustomerId", 1)], "sparse": True, "name": "ix_billing_customer"},
    ],
    "schema_migrations": [
        {"keys": [("version", 1)], "unique": True, "name": "uq_migrations_version"},
    ],
}


async def apply_indexes(db) -> Dict[str, int]:
    """Create every index in :data:`INDEX_SPECIFICATION`.

    Safe to run on every startup: MongoDB treats an existing matching index as
    a no-op. Failures are logged and counted rather than raised, so a single
    problematic collection (e.g. legacy duplicate values blocking a unique
    index) cannot take the whole application down at boot.
    """
    created = 0
    failed: List[str] = []

    for collection_name, specs in INDEX_SPECIFICATION.items():
        collection = db[collection_name]
        for spec in specs:
            try:
                await collection.create_index(
                    spec["keys"],
                    name=spec["name"],
                    unique=spec.get("unique", False),
                    sparse=spec.get("sparse", False),
                )
                created += 1
            except Exception as exc:  # pragma: no cover - depends on live data
                failed.append(f"{collection_name}.{spec['name']}")
                logger.error(
                    "Index creation failed",
                    extra={
                        "collection": collection_name,
                        "index": spec["name"],
                        "error": str(exc),
                    },
                )

    if failed:
        logger.warning("Some indexes could not be created: %s", ", ".join(failed))

    return {"created": created, "failed": len(failed)}
