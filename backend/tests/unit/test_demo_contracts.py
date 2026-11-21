"""New trials receive four fictional contracts without an upload."""

import asyncio
from datetime import datetime, timezone
from pathlib import Path

import pytest

from demo_contracts import (
    DEMO_KEYS,
    build_demo_contracts,
    party_name,
    seed_demo_contracts,
)

BACKEND = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 11, 21, 12, 0, tzinfo=timezone.utc)


class _Cursor:
    def __init__(self, rows):
        self._rows = rows

    async def to_list(self, length=None):
        if length is None:
            return list(self._rows)
        return list(self._rows)[:length]


class _Contracts:
    def __init__(self):
        self.docs = []
        self.fail_insert = False

    def find(self, query, projection=None):
        org = query.get("organizationId")
        keys = set((query.get("demoKey") or {}).get("$in") or [])
        rows = [
            doc
            for doc in self.docs
            if doc.get("organizationId") == org and doc.get("demoKey") in keys
        ]
        if projection:
            rows = [
                {key: doc.get(key) for key, keep in projection.items() if keep}
                for doc in rows
            ]
        return _Cursor(rows)

    async def insert_many(self, docs, ordered=True):
        if self.fail_insert:
            raise RuntimeError("database unavailable")
        self.docs.extend(docs)
        return docs


class _Db:
    def __init__(self):
        self.contracts = _Contracts()


def test_party_name_is_one_line_and_has_a_fallback():
    assert party_name("  Harbor\nLane  ") == "Harbor Lane"
    assert party_name("   ") == "Your organization"
    assert party_name("") == "Your organization"
    assert len(party_name("A" * 200)) == 80


def test_four_sample_contracts_are_plain_text():
    docs = build_demo_contracts("org-1", "user-1", "North Glass Ltd.", now=NOW)
    assert [doc["demoKey"] for doc in docs] == list(DEMO_KEYS)
    assert len({doc["id"] for doc in docs}) == 4
    kinds = {doc["contractType"] for doc in docs}
    assert kinds == {"Retainer", "MSA", "Statement of Work", "Vendor Agreement"}
    for doc in docs:
        assert doc["organizationId"] == "org-1"
        assert doc["uploadedBy"] == "user-1"
        assert doc["status"] == "active"
        assert doc["isDemo"] is True
        assert doc["tags"] == ["sample"]
        assert doc["storageKey"] is None
        assert doc["mimeType"] == "text/plain"
        assert doc["fileName"].endswith(".txt")
        assert doc["riskLevel"] in {"low", "medium"}
        text = doc["originalText"]
        assert isinstance(text, str)
        assert "\x00" not in text
        encoded = text.encode("utf-8")
        assert doc["fileSize"] == len(encoded)
        assert text.startswith("SAMPLE CONTRACT\n")
        assert "North Glass Ltd." in text
        assert doc["counterparty"] in text
        assert "fictional" in text.lower()
        assert doc["effectiveDate"] < doc["expiryDate"]
        assert doc["expiryDate"] >= "2027-01-01"
        summary = doc["aiAnalysis"]["summary"]
        assert "North Glass Ltd." in summary
        assert doc["counterparty"] in doc["aiAnalysis"]["parties"]
        assert doc["aiAnalysis"]["keyTerms"]
        assert doc["aiAnalysisStatus"] == "success"
    assert "ocr" not in "\n".join(doc["originalText"] for doc in docs).lower()


def test_blank_organization_name_uses_the_fallback_party():
    docs = build_demo_contracts("org-1", "user-1", "  \n ", now=NOW)
    assert all("Your organization" in doc["originalText"] for doc in docs)


def test_samples_do_not_touch_the_trial_counter_or_storage():
    source = (BACKEND / "demo_contracts.py").read_text(encoding="utf-8").lower()
    assert "ocr" not in source
    assert "trialcontractsused" not in source
    assert "reserve_trial_upload" not in source
    assert "upload_file_to_s3" not in source
    assert "analyze_contract" not in source


def test_register_seeds_after_the_account_is_saved():
    source = (BACKEND / "routes" / "auth.py").read_text(encoding="utf-8")
    assert "from demo_contracts import seed_demo_contracts" in source
    insert_at = source.index("await db.users.insert_one(user_doc)")
    seed_at = source.index("await seed_demo_contracts(")
    token_at = source.index("access_token = create_access_token(")
    assert insert_at < seed_at < token_at


def test_seed_inserts_once_per_organization():
    async def run():
        db = _Db()
        first = await seed_demo_contracts(db, "org-1", "user-1", "North Glass Ltd.")
        second = await seed_demo_contracts(db, "org-1", "user-1", "North Glass Ltd.")
        other = await seed_demo_contracts(db, "org-2", "user-2", "Other Co")
        return first, second, other, db.contracts.docs

    first, second, other, docs = asyncio.run(run())
    assert first == 4
    assert second == 0
    assert other == 4
    assert len(docs) == 8
    assert {doc["organizationId"] for doc in docs} == {"org-1", "org-2"}


def test_seed_fills_only_the_missing_samples():
    async def run():
        db = _Db()
        db.contracts.docs.append(
            {"organizationId": "org-1", "demoKey": "retainer", "id": "kept"}
        )
        added = await seed_demo_contracts(db, "org-1", "user-1", "North Glass Ltd.")
        return added, db.contracts.docs

    added, docs = asyncio.run(run())
    assert added == 3
    keys = [doc["demoKey"] for doc in docs if doc["organizationId"] == "org-1"]
    assert keys.count("retainer") == 1
    assert set(keys) == set(DEMO_KEYS)


def test_seed_failure_does_not_raise():
    async def run():
        db = _Db()
        db.contracts.fail_insert = True
        return await seed_demo_contracts(db, "org-1", "user-1", "North Glass Ltd.")

    assert asyncio.run(run()) == 0


@pytest.mark.parametrize("org_id,user_id", [("", "user-1"), ("org-1", ""), (None, "u")])
def test_seed_skips_a_missing_owner(org_id, user_id):
    async def run():
        db = _Db()
        added = await seed_demo_contracts(db, org_id, user_id, "North Glass Ltd.")
        return added, db.contracts.docs

    added, docs = asyncio.run(run())
    assert added == 0
    assert docs == []
