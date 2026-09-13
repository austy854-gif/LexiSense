#!/usr/bin/env python3
"""
Database seed script for LexiSense.
Run this on fresh deployments to create default templates and verify indexes.
"""
import asyncio
import os
import sys
from pathlib import Path

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent))

from motor.motor_asyncio import AsyncIOMotorClient
from models.template import Template, TemplateCreate
from models.organization import Organization
from models.user import User
from utils.auth import hash_password
import uuid
from datetime import datetime, timezone


DEFAULT_TEMPLATES = [
    {
        "name": "Non-Disclosure Agreement (NDA)",
        "description": "Standard mutual NDA for protecting confidential information",
        "contractType": "NDA",
        "content": """NON-DISCLOSURE AGREEMENT

This Non-Disclosure Agreement ("Agreement") is entered into as of [EFFECTIVE_DATE] by and between:

[PARTY_A_NAME], a [PARTY_A_STATE] [PARTY_A_ENTITY_TYPE] with its principal place of business at [PARTY_A_ADDRESS] ("Disclosing Party")

and

[PARTY_B_NAME], a [PARTY_B_STATE] [PARTY_B_ENTITY_TYPE] with its principal place of business at [PARTY_B_ADDRESS] ("Receiving Party")

(collectively, the "Parties" and each individually a "Party").

1. DEFINITION OF CONFIDENTIAL INFORMATION
"Confidential Information" means any information disclosed by Disclosing Party to Receiving Party, either directly or indirectly, in writing, orally, or by inspection of tangible objects, that is designated as "Confidential," "Proprietary," or some similar designation, or that reasonably should be understood to be confidential given the nature of the information and the circumstances of disclosure.

2. OBLIGATIONS OF RECEIVING PARTY
Receiving Party agrees to:
(a) Hold Confidential Information in strict confidence;
(b) Not disclose Confidential Information to any third party without prior written consent;
(c) Use Confidential Information solely for the purpose of [PURPOSE];
(d) Limit access to Confidential Information to employees/contractors with a need to know.

3. EXCLUSIONS
This Agreement does not apply to information that:
(a) Is or becomes publicly known through no fault of Receiving Party;
(b) Was known to Receiving Party prior to disclosure;
(c) Is independently developed by Receiving Party;
(d) Is received from a third party without breach of obligation.

4. TERM
This Agreement remains in effect for [TERM_YEARS] years from the Effective Date.

5. RETURN OF MATERIALS
Upon request or termination, Receiving Party will return or destroy all Confidential Information.

6. GOVERNING LAW
This Agreement shall be governed by the laws of [GOVERNING_LAW_STATE].

IN WITNESS WHEREOF, the Parties have executed this Agreement as of the Effective Date.

[PARTY_A_NAME]                    [PARTY_B_NAME]
By: _________________________    By: _________________________
Name: _________________________  Name: _________________________
Title: _________________________  Title: _________________________
Date: _________________________  Date: _________________________""",
        "fields": [
            {"name": "EFFECTIVE_DATE", "label": "Effective Date", "type": "date", "required": True},
            {"name": "PARTY_A_NAME", "label": "Disclosing Party Name", "type": "text", "required": True},
            {"name": "PARTY_A_STATE", "label": "Disclosing Party State", "type": "text", "required": True},
            {"name": "PARTY_A_ENTITY_TYPE", "label": "Disclosing Party Entity Type", "type": "text", "required": True},
            {"name": "PARTY_A_ADDRESS", "label": "Disclosing Party Address", "type": "textarea", "required": True},
            {"name": "PARTY_B_NAME", "label": "Receiving Party Name", "type": "text", "required": True},
            {"name": "PARTY_B_STATE", "label": "Receiving Party State", "type": "text", "required": True},
            {"name": "PARTY_B_ENTITY_TYPE", "label": "Receiving Party Entity Type", "type": "text", "required": True},
            {"name": "PARTY_B_ADDRESS", "label": "Receiving Party Address", "type": "textarea", "required": True},
            {"name": "PURPOSE", "label": "Purpose of Disclosure", "type": "textarea", "required": True},
            {"name": "TERM_YEARS", "label": "Term (Years)", "type": "number", "required": True},
            {"name": "GOVERNING_LAW_STATE", "label": "Governing Law State", "type": "text", "required": True},
        ],
        "tags": ["nda", "confidentiality", "standard"],
        "isDefault": True
    },
    {
        "name": "Service Agreement",
        "description": "General service agreement for professional services",
        "contractType": "Service Agreement",
        "content": """SERVICE AGREEMENT

This Service Agreement ("Agreement") is entered into as of [EFFECTIVE_DATE] by and between:

[CLIENT_NAME], a [CLIENT_STATE] [CLIENT_ENTITY_TYPE] with its principal place of business at [CLIENT_ADDRESS] ("Client")

and

[PROVIDER_NAME], a [PROVIDER_STATE] [PROVIDER_ENTITY_TYPE] with its principal place of business at [PROVIDER_ADDRESS] ("Provider")

1. SERVICES
Provider shall perform the services described in Exhibit A ("Services") in a professional and workmanlike manner.

2. COMPENSATION
Client shall pay Provider [COMPENSATION_AMOUNT] [COMPENSATION_TYPE] for the Services. Payment terms: [PAYMENT_TERMS].

3. TERM AND TERMINATION
This Agreement commences on [EFFECTIVE_DATE] and continues until [END_DATE] unless terminated earlier per Section 3.2.
Either party may terminate with [NOTICE_DAYS] days written notice.

4. INTELLECTUAL PROPERTY
All work product created under this Agreement shall be owned by [IP_OWNER].

5. CONFIDENTIALITY
Both parties shall maintain confidentiality of proprietary information.

6. INDEMNIFICATION
[INDEMNIFICATION_CLAUSE]

7. LIMITATION OF LIABILITY
Neither party shall be liable for indirect, incidental, or consequential damages.

8. GOVERNING LAW
This Agreement shall be governed by the laws of [GOVERNING_LAW_STATE].

IN WITNESS WHEREOF, the parties have executed this Agreement.

[CLIENT_NAME]                    [PROVIDER_NAME]
By: _________________________    By: _________________________
Name: _________________________  Name: _________________________
Title: _________________________  Title: _________________________
Date: _________________________  Date: _________________________""",
        "fields": [
            {"name": "EFFECTIVE_DATE", "label": "Effective Date", "type": "date", "required": True},
            {"name": "CLIENT_NAME", "label": "Client Name", "type": "text", "required": True},
            {"name": "CLIENT_STATE", "label": "Client State", "type": "text", "required": True},
            {"name": "CLIENT_ENTITY_TYPE", "label": "Client Entity Type", "type": "text", "required": True},
            {"name": "CLIENT_ADDRESS", "label": "Client Address", "type": "textarea", "required": True},
            {"name": "PROVIDER_NAME", "label": "Provider Name", "type": "text", "required": True},
            {"name": "PROVIDER_STATE", "label": "Provider State", "type": "text", "required": True},
            {"name": "PROVIDER_ENTITY_TYPE", "label": "Provider Entity Type", "type": "text", "required": True},
            {"name": "PROVIDER_ADDRESS", "label": "Provider Address", "type": "textarea", "required": True},
            {"name": "COMPENSATION_AMOUNT", "label": "Compensation Amount", "type": "text", "required": True},
            {"name": "COMPENSATION_TYPE", "label": "Compensation Type", "type": "select", "options": ["hourly", "fixed fee", "monthly retainer"], "required": True},
            {"name": "PAYMENT_TERMS", "label": "Payment Terms", "type": "text", "required": True},
            {"name": "END_DATE", "label": "End Date", "type": "date", "required": True},
            {"name": "NOTICE_DAYS", "label": "Notice Period (Days)", "type": "number", "required": True},
            {"name": "IP_OWNER", "label": "IP Owner", "type": "select", "options": ["Client", "Provider"], "required": True},
            {"name": "INDEMNIFICATION_CLAUSE", "label": "Indemnification Clause", "type": "textarea", "required": False},
            {"name": "GOVERNING_LAW_STATE", "label": "Governing Law State", "type": "text", "required": True},
        ],
        "tags": ["services", "professional", "standard"],
        "isDefault": True
    },
    {
        "name": "Employment Agreement",
        "description": "Standard employment agreement for full-time employees",
        "contractType": "Employment",
        "content": """EMPLOYMENT AGREEMENT

This Employment Agreement ("Agreement") is made as of [START_DATE] by and between:

[COMPANY_NAME], a [COMPANY_STATE] [COMPANY_ENTITY_TYPE] with its principal place of business at [COMPANY_ADDRESS] ("Company")

and

[EMPLOYEE_NAME], residing at [EMPLOYEE_ADDRESS] ("Employee")

1. POSITION AND DUTIES
Employee shall serve as [JOB_TITLE] and perform duties as assigned by Company.

2. COMPENSATION
Base Salary: $[ANNUAL_SALARY] per year, paid [PAY_FREQUENCY].
Bonus: [BONUS_DETAILS]
Benefits: [BENEFITS_SUMMARY]

3. TERM
Employment begins on [START_DATE] and continues [EMPLOYMENT_TYPE] until terminated.

4. TERMINATION
Either party may terminate with [NOTICE_DAYS] days notice. Company may terminate immediately for Cause.

5. CONFIDENTIALITY
Employee shall not disclose Company confidential information.

6. INTELLECTUAL PROPERTY
All work product belongs to Company.

7. NON-COMPETE
[NON_COMPETE_CLAUSE]

8. GOVERNING LAW
This Agreement shall be governed by the laws of [GOVERNING_LAW_STATE].

IN WITNESS WHEREOF, the parties have executed this Agreement.

[COMPANY_NAME]              [EMPLOYEE_NAME]
By: ______________________  ______________________
Name: _____________________  Date: ________________
Title: _____________________
Date: _____________________""",
        "fields": [
            {"name": "START_DATE", "label": "Start Date", "type": "date", "required": True},
            {"name": "COMPANY_NAME", "label": "Company Name", "type": "text", "required": True},
            {"name": "COMPANY_STATE", "label": "Company State", "type": "text", "required": True},
            {"name": "COMPANY_ENTITY_TYPE", "label": "Company Entity Type", "type": "text", "required": True},
            {"name": "COMPANY_ADDRESS", "label": "Company Address", "type": "textarea", "required": True},
            {"name": "EMPLOYEE_NAME", "label": "Employee Name", "type": "text", "required": True},
            {"name": "EMPLOYEE_ADDRESS", "label": "Employee Address", "type": "textarea", "required": True},
            {"name": "JOB_TITLE", "label": "Job Title", "type": "text", "required": True},
            {"name": "ANNUAL_SALARY", "label": "Annual Salary", "type": "number", "required": True},
            {"name": "PAY_FREQUENCY", "label": "Pay Frequency", "type": "select", "options": ["bi-weekly", "monthly", "semi-monthly"], "required": True},
            {"name": "BONUS_DETAILS", "label": "Bonus Details", "type": "textarea", "required": False},
            {"name": "BENEFITS_SUMMARY", "label": "Benefits Summary", "type": "textarea", "required": False},
            {"name": "EMPLOYMENT_TYPE", "label": "Employment Type", "type": "select", "options": ["at-will", "fixed term"], "required": True},
            {"name": "NOTICE_DAYS", "label": "Notice Period (Days)", "type": "number", "required": True},
            {"name": "NON_COMPETE_CLAUSE", "label": "Non-Compete Clause", "type": "textarea", "required": False},
            {"name": "GOVERNING_LAW_STATE", "label": "Governing Law State", "type": "text", "required": True},
        ],
        "tags": ["employment", "hr", "standard"],
        "isDefault": True
    },
    {
        "name": "Master Services Agreement (MSA)",
        "description": "Framework agreement for ongoing service relationships",
        "contractType": "Service Agreement",
        "content": """MASTER SERVICES AGREEMENT

This Master Services Agreement ("Agreement") is entered into as of [EFFECTIVE_DATE] by and between:

[CLIENT_NAME] ("Client") and [PROVIDER_NAME] ("Provider")

1. SCOPE
Provider will perform Services as described in Statements of Work ("SOWs") referencing this Agreement.

2. FEES AND PAYMENT
Fees per SOW. Payment: [PAYMENT_TERMS]. Late fees: [LATE_FEE]% per month.

3. TERM
Initial term: [INITIAL_TERM_MONTHS] months. Auto-renews for [RENEWAL_TERM_MONTHS] months unless terminated.

4. TERMINATION
[TERMINATION_CLAUSE]

5. INTELLECTUAL PROPERTY
Deliverables: [IP_OWNERSHIP]. Background IP retained by each party.

6. CONFIDENTIALITY
Standard mutual confidentiality. Term: [CONFIDENTIALITY_YEARS] years.

7. WARRANTIES
Provider warrants Services performed professionally. [ADDITIONAL_WARRANTIES]

8. INDEMNIFICATION
[INDEMNIFICATION_CLAUSE]

9. LIMITATION OF LIABILITY
Cap: [LIABILITY_CAP]. No consequential damages.

10. GOVERNING LAW
[GOVERNING_LAW_STATE].

IN WITNESS WHEREOF, parties execute this Agreement.

[CLIENT_NAME]              [PROVIDER_NAME]
By: ______________________  ______________________
Name: _____________________  Name: _____________________
Title: _____________________  Title: _____________________
Date: _____________________  Date: _____________________""",
        "fields": [
            {"name": "EFFECTIVE_DATE", "label": "Effective Date", "type": "date", "required": True},
            {"name": "CLIENT_NAME", "label": "Client Name", "type": "text", "required": True},
            {"name": "PROVIDER_NAME", "label": "Provider Name", "type": "text", "required": True},
            {"name": "PAYMENT_TERMS", "label": "Payment Terms", "type": "text", "required": True},
            {"name": "LATE_FEE", "label": "Late Fee %", "type": "number", "required": True},
            {"name": "INITIAL_TERM_MONTHS", "label": "Initial Term (Months)", "type": "number", "required": True},
            {"name": "RENEWAL_TERM_MONTHS", "label": "Renewal Term (Months)", "type": "number", "required": True},
            {"name": "TERMINATION_CLAUSE", "label": "Termination Clause", "type": "textarea", "required": True},
            {"name": "IP_OWNERSHIP", "label": "IP Ownership", "type": "select", "options": ["Client", "Provider", "Joint"], "required": True},
            {"name": "CONFIDENTIALITY_YEARS", "label": "Confidentiality Term (Years)", "type": "number", "required": True},
            {"name": "ADDITIONAL_WARRANTIES", "label": "Additional Warranties", "type": "textarea", "required": False},
            {"name": "INDEMNIFICATION_CLAUSE", "label": "Indemnification Clause", "type": "textarea", "required": True},
            {"name": "LIABILITY_CAP", "label": "Liability Cap", "type": "text", "required": True},
            {"name": "GOVERNING_LAW_STATE", "label": "Governing Law State", "type": "text", "required": True},
        ],
        "tags": ["msa", "master", "framework", "ongoing"],
        "isDefault": True
    },
]


async def seed_database():
    """Seed the database with default templates and verify setup."""
    mongo_url = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
    db_name = os.environ.get("DB_NAME", "lexisense")
    
    client = AsyncIOMotorClient(mongo_url)
    db = client[db_name]
    
    print(f"Connected to MongoDB: {mongo_url}/{db_name}")
    
    # Check if templates already exist
    existing_count = await db.templates.count_documents({"isDefault": True})
    if existing_count > 0:
        print(f"Default templates already exist ({existing_count}). Skipping seed.")
    else:
        print("Seeding default templates...")
        for template_data in DEFAULT_TEMPLATES:
            template = Template(**template_data)
            await db.templates.insert_one(template.model_dump())
            print(f"  Created: {template.name}")
        print(f"Seeded {len(DEFAULT_TEMPLATES)} default templates")
    
    # Verify indexes
    print("\nVerifying indexes...")
    collections_to_check = [
        "users", "contracts", "organizations", "invitations",
        "contract_versions", "expiration_alerts", "templates",
        "audit_logs", "notifications"
    ]
    
    for coll_name in collections_to_check:
        indexes = await db[coll_name].index_information()
        print(f"  {coll_name}: {len(indexes)} indexes")
    
    # Ensure S3 bucket (optional - will warn if not configured)
    from services.storage_service import ensure_bucket_exists
    print("\nChecking S3 bucket...")
    bucket_ok = await ensure_bucket_exists()
    if bucket_ok:
        print("  S3 bucket verified")
    else:
        print("  S3 bucket not configured (mock mode)")
    
    print("\n✅ Database seed complete!")
    client.close()


if __name__ == "__main__":
    asyncio.run(seed_database())