"""Four fictional contracts for a brand-new trial.

Registration inserts them so the dashboard and contract list are usable
before anyone uploads a file. The body is plain UTF-8 text stored on the
contract and shown on the Document tab. Nothing is sent to object storage,
and the trial upload counter is left untouched.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

DEMO_KEYS = (
    "retainer",
    "msa",
    "statement-of-work",
    "vendor-agreement",
)

_SAMPLE_NOTE = (
    "SAMPLE CONTRACT\n"
    "Fictional parties only. This text is stored so a new trial can open "
    "a contract and read it. It is not a real agreement, and no file was uploaded.\n"
)


def party_name(name: str) -> str:
    """A single-line party name safe to place in sample text."""
    cleaned = " ".join((name or "").split())
    cleaned = "".join(ch for ch in cleaned if ch.isprintable())
    cleaned = cleaned.strip()
    if not cleaned:
        return "Your organization"
    return cleaned[:80]


def _stamp(now: datetime, offset_seconds: int) -> str:
    return (now + timedelta(seconds=offset_seconds)).isoformat()


def _analysis(
    summary: str,
    parties: list[str],
    key_terms: list[str],
    risk: str,
    severity: str,
    recommendation: str,
    advice: str,
) -> dict:
    return {
        "summary": summary,
        "parties": parties,
        "keyTerms": key_terms,
        "risks": [
            {
                "risk": risk,
                "severity": severity,
                "recommendation": recommendation,
            }
        ],
        "recommendations": [advice],
    }


def _contract(
    *,
    organization_id: str,
    user_id: str,
    now: datetime,
    offset_seconds: int,
    demo_key: str,
    title: str,
    counterparty: str,
    contract_type: str,
    value: str,
    effective_date: str,
    expiry_date: str,
    risk_level: str,
    file_name: str,
    body: str,
    analysis: dict,
) -> dict:
    text = _SAMPLE_NOTE + "\n" + body.strip() + "\n"
    encoded = text.encode("utf-8")
    moment = _stamp(now, offset_seconds)
    return {
        "id": str(uuid.uuid4()),
        "organizationId": organization_id,
        "uploadedBy": user_id,
        "title": title,
        "counterparty": counterparty,
        "contractType": contract_type,
        "status": "active",
        "value": value,
        "effectiveDate": effective_date,
        "expiryDate": expiry_date,
        "riskLevel": risk_level,
        "originalText": text,
        "aiAnalysis": analysis,
        "aiAnalysisStatus": "success",
        "storageKey": None,
        "fileName": file_name,
        "fileSize": len(encoded),
        "mimeType": "text/plain",
        "tags": ["sample"],
        "workflowHistory": [],
        "approvedBy": None,
        "approvedAt": None,
        "createdAt": moment,
        "updatedAt": moment,
        "demoKey": demo_key,
        "isDemo": True,
    }


def build_demo_contracts(
    organization_id: str,
    user_id: str,
    organization_name: str,
    now: datetime | None = None,
) -> list[dict]:
    """Return the four sample contract documents. Does not write anything."""
    org = party_name(organization_name)
    clock = now or datetime.now(timezone.utc)
    if clock.tzinfo is None:
        clock = clock.replace(tzinfo=timezone.utc)

    retainer_body = f"""
RETAINER AGREEMENT

This retainer is between {org} ("Client") and Harbor Lane Studio ("Studio").
Harbor Lane Studio is a fictional design studio.

1. Services. The Studio provides up to twenty hours each month of layout and
brand work for the Client. Hours past twenty need a short written change
before the extra work starts.

2. Term. This retainer starts on 1 November 2026 and ends on 31 October 2027.
Either party may end it with thirty days' written notice.

3. Fees. The Client pays $4,800 on the first business day of each month.
Unused hours do not carry forward. Invoices are due within fifteen days.

4. Ownership. Work the Studio delivers, and the Client has paid for, belongs
to the Client. The Studio keeps its existing tools and typefaces.

5. Confidentiality. Each party keeps the other's non-public business
information private and uses it only to perform this retainer.

6. Liability. Each party's total liability under this retainer is limited to
the fees paid in the three months before a claim, except for fees that are
still unpaid.
""".strip()

    msa_body = f"""
MASTER SERVICES AGREEMENT

This master services agreement is between {org} ("Customer") and Pinenook
Analytics Co. ("Supplier"). Pinenook Analytics Co. is a fictional company.
Project work is described in statements of work under this agreement.

1. Services. The Supplier performs the services named in each statement of
work. A statement of work controls the scope, price, and dates for that
project. This agreement controls everything else.

2. Term. This agreement starts on 1 September 2026 and ends on 31 August 2028,
unless ended earlier. Either party may end it for a material breach that is
not fixed within thirty days after written notice.

3. Fees. The Customer pays the fees in each statement of work. Invoices are
due within thirty days. A late amount may accrue interest at one percent
per month, or the legal maximum if that is lower.

4. Customer data. The Customer owns its data. The Supplier uses that data
only to perform the services and returns or deletes it when the agreement
ends, except for copies it must keep by law.

5. Confidentiality. Each party protects the other's non-public information
with the same care it uses for its own, and at least reasonable care.

6. Liability. Except for unpaid fees, confidentiality, or injury caused by
willful misconduct, each party's total liability is limited to the fees paid
under this agreement in the twelve months before the claim. Neither party is
liable for lost profits or indirect damages.
""".strip()

    sow_body = f"""
STATEMENT OF WORK

This statement of work is under the master services agreement between {org}
("Customer") and Pinenook Analytics Co. ("Supplier"). Pinenook Analytics Co.
is a fictional company. If this statement conflicts with that agreement on
scope or price, this statement wins. On any other topic, the agreement wins.

1. Project. The Supplier will set up a contract-renewal report for the
Customer: a list of agreements that end within ninety days, the counterparty,
and the renewal owner.

2. Delivery. A first version is due on 30 November 2026. A corrected version
is due within ten business days after the Customer sends one round of written
comments.

3. Price. The fixed price is $22,400, billed in two equal parts: one when work
starts, and one when the corrected version is delivered.

4. Term. This statement starts on 1 October 2026 and ends on 31 March 2027.
It does not renew on its own.

5. Assumptions. The Customer will name one reviewer and provide sample
agreement dates. Delays in that review move the delivery dates by the same
number of business days.
""".strip()

    vendor_body = f"""
VENDOR AGREEMENT

This vendor agreement is between {org} ("Buyer") and Red Cedar Supplies Ltd.
("Vendor"). Red Cedar Supplies Ltd. is a fictional supplier of filing supplies.

1. Goods. The Vendor sells the Buyer archival file folders, boxes, and labels
against written orders the Buyer sends during the term. An order is accepted
when the Vendor confirms it in writing.

2. Term. This agreement starts on 1 August 2026 and ends on 31 July 2027.
The Buyer may end it with thirty days' written notice. Orders already accepted
are still filled.

3. Price. The annual estimate is $18,600. Each order uses the Vendor's price
list from the order date. The Vendor may change the list on thirty days'
notice, and the change does not apply to orders already accepted. Invoices
are due within thirty days.

4. Delivery. The Vendor ships to the Buyer's office named on the order. Risk
passes when the Buyer receives the goods. The Buyer may refuse goods that do
not match the order if it says so within ten business days.

5. Warranty. The Vendor warrants that it has the right to sell the goods and
that they match the description on the order. The Buyer's remedy is replacement
or a refund of the price paid for the rejected goods.

6. Liability. The Vendor's total liability under this agreement is limited to
the amount the Buyer paid for the goods that gave rise to the claim.
""".strip()

    specs = [
        {
            "offset_seconds": 3,
            "demo_key": "retainer",
            "title": "Sample retainer — Harbor Lane Studio",
            "counterparty": "Harbor Lane Studio",
            "contract_type": "Retainer",
            "value": "$4,800 per month",
            "effective_date": "2026-11-01",
            "expiry_date": "2027-10-31",
            "risk_level": "low",
            "file_name": "sample-retainer-harbor-lane.txt",
            "body": retainer_body,
            "analysis": _analysis(
                summary=(
                    f"Sample retainer. {org} pays Harbor Lane Studio $4,800 a "
                    "month for up to twenty hours of design work through "
                    "31 October 2027. Open the Document tab to read the full sample."
                ),
                parties=[org, "Harbor Lane Studio"],
                key_terms=[
                    "Up to twenty hours of design work each month",
                    "Fee of $4,800 due on the first business day of the month",
                    "Ends 31 October 2027, or sooner on thirty days' notice",
                ],
                risk="Unused hours expire at the end of the month.",
                severity="low",
                recommendation="Track hours before the month ends if the work is still needed.",
                advice="Read the fee and notice clauses before treating this as a live retainer.",
            ),
        },
        {
            "offset_seconds": 2,
            "demo_key": "msa",
            "title": "Sample MSA — Pinenook Analytics",
            "counterparty": "Pinenook Analytics Co.",
            "contract_type": "MSA",
            "value": "Fees set in each statement of work",
            "effective_date": "2026-09-01",
            "expiry_date": "2028-08-31",
            "risk_level": "medium",
            "file_name": "sample-msa-pinenook.txt",
            "body": msa_body,
            "analysis": _analysis(
                summary=(
                    f"Sample master services agreement between {org} and "
                    "Pinenook Analytics Co., through 31 August 2028. Project "
                    "scope and price live in statements of work. Open the "
                    "Document tab to read the full sample."
                ),
                parties=[org, "Pinenook Analytics Co."],
                key_terms=[
                    "Statements of work set scope, price, and dates",
                    "Invoices are due within thirty days",
                    "Liability cap equal to fees paid in the prior twelve months",
                ],
                risk=(
                    "Liability for lost profits is excluded, and the overall cap "
                    "is the fees paid in the prior twelve months."
                ),
                severity="medium",
                recommendation="Compare the cap with the size of the project before relying on it.",
                advice="Pair this agreement with the sample statement of work.",
            ),
        },
        {
            "offset_seconds": 1,
            "demo_key": "statement-of-work",
            "title": "Sample statement of work — Pinenook renewal report",
            "counterparty": "Pinenook Analytics Co.",
            "contract_type": "Statement of Work",
            "value": "$22,400",
            "effective_date": "2026-10-01",
            "expiry_date": "2027-03-31",
            "risk_level": "low",
            "file_name": "sample-sow-pinenook.txt",
            "body": sow_body,
            "analysis": _analysis(
                summary=(
                    f"Sample statement of work. Pinenook Analytics Co. builds a "
                    f"renewal report for {org} for a fixed price of $22,400, "
                    "due in two parts. Open the Document tab to read the full sample."
                ),
                parties=[org, "Pinenook Analytics Co."],
                key_terms=[
                    "Fixed price of $22,400 in two equal invoices",
                    "First version due 30 November 2026",
                    "One round of written comments, then ten business days to correct",
                ],
                risk="A slow review by the Customer moves the delivery date by the same delay.",
                severity="low",
                recommendation="Name the reviewer before the first version is due.",
                advice="Read this next to the sample master services agreement.",
            ),
        },
        {
            "offset_seconds": 0,
            "demo_key": "vendor-agreement",
            "title": "Sample vendor agreement — Red Cedar Supplies",
            "counterparty": "Red Cedar Supplies Ltd.",
            "contract_type": "Vendor Agreement",
            "value": "$18,600 per year",
            "effective_date": "2026-08-01",
            "expiry_date": "2027-07-31",
            "risk_level": "low",
            "file_name": "sample-vendor-red-cedar.txt",
            "body": vendor_body,
            "analysis": _analysis(
                summary=(
                    f"Sample vendor agreement. {org} may order filing supplies "
                    "from Red Cedar Supplies Ltd. through 31 July 2027. The "
                    "annual estimate is $18,600. Open the Document tab to read "
                    "the full sample."
                ),
                parties=[org, "Red Cedar Supplies Ltd."],
                key_terms=[
                    "Orders are accepted only when the Vendor confirms them",
                    "Annual estimate of $18,600",
                    "Buyer may reject goods that do not match the order within ten business days",
                ],
                risk="The price list can change on thirty days' notice for new orders.",
                severity="low",
                recommendation="Confirm the price when each order is placed.",
                advice="This sample is for reading the product, not for placing an order.",
            ),
        },
    ]

    documents = [
        _contract(
            organization_id=organization_id,
            user_id=user_id,
            now=clock,
            offset_seconds=spec["offset_seconds"],
            demo_key=spec["demo_key"],
            title=spec["title"],
            counterparty=spec["counterparty"],
            contract_type=spec["contract_type"],
            value=spec["value"],
            effective_date=spec["effective_date"],
            expiry_date=spec["expiry_date"],
            risk_level=spec["risk_level"],
            file_name=spec["file_name"],
            body=spec["body"],
            analysis=spec["analysis"],
        )
        for spec in specs
    ]
    return documents


async def seed_demo_contracts(
    database,
    organization_id: str,
    user_id: str,
    organization_name: str,
) -> int:
    """Insert any sample contracts this organization does not already have.

    Safe to call again. A database error is logged and reported as zero so
    creating the account can still finish. Returns how many documents were
    inserted.
    """
    if not organization_id or not user_id:
        return 0
    try:
        wanted = build_demo_contracts(organization_id, user_id, organization_name)
        keys = [doc["demoKey"] for doc in wanted]
        found = await database.contracts.find(
            {"organizationId": organization_id, "demoKey": {"$in": keys}},
            {"_id": 0, "demoKey": 1},
        ).to_list(length=len(keys))
        present = {row.get("demoKey") for row in found if isinstance(row, dict)}
        missing = [doc for doc in wanted if doc["demoKey"] not in present]
        if not missing:
            return 0
        await database.contracts.insert_many(missing, ordered=True)
        return len(missing)
    except Exception:
        logger.exception(
            "Could not add sample contracts for organization %s",
            organization_id,
        )
        return 0
