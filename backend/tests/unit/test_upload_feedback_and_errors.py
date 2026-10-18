"""Upload outcomes are honest and never leak exception text to users.

Covers:
* single and bulk upload store a friendly ``aiAnalysisError`` (never the raw
  exception) and log the technical detail server-side;
* bulk per-file failures from unexpected exceptions return friendly copy;
* trial-ended copy does not state a trial length.
"""
import logging
from datetime import datetime, timedelta, timezone

import pytest

from routes import billing as billing_route
from routes import contracts as contracts_route
from tests.unit.conftest import API_PREFIX, auth_headers, make_user

SECRET = "pymongo.errors.ServerSelectionTimeoutError: cluster0-shard-00.abcde.mongodb.net:27017 secret-token-123"


@pytest.fixture
def uploader(org_id):
    return make_user(org_id, role="user")


@pytest.fixture
def patched_pipeline(monkeypatch, db):
    """Stub every external side effect of the upload pipeline."""
    released = []

    async def _extract(content, mime):
        return "This Agreement is made between A and B."

    async def _reserve(org_id):
        return True, ""

    async def _release(org_id):
        released.append(org_id)

    async def _s3(content, filename, org_id, mime):
        return f"{org_id}/{filename}"

    async def _log_action(**kwargs):
        return None

    monkeypatch.setattr(contracts_route, "extract_text_from_file", _extract)
    monkeypatch.setattr(contracts_route, "reserve_trial_upload_atomic", _reserve)
    monkeypatch.setattr(contracts_route, "release_trial_upload", _release)
    monkeypatch.setattr(contracts_route, "upload_file_to_s3", _s3)
    monkeypatch.setattr(contracts_route, "log_action", _log_action)
    return {"released": released}


def _set_analysis(monkeypatch, behaviour):
    async def _analyze(text):
        if isinstance(behaviour, Exception):
            raise behaviour
        return behaviour

    monkeypatch.setattr(contracts_route, "analyze_contract", _analyze)


async def _upload(client, user, name="msa.txt"):
    return await client.post(
        f"{API_PREFIX}/contracts",
        headers=auth_headers(user),
        files={"file": (name, b"This Agreement is made between A and B.", "text/plain")},
        data={"title": "MSA", "contractType": "General"},
    )


class TestSingleUpload:
    async def test_success_reports_success(self, client, db, uploader, patched_pipeline, monkeypatch):
        _set_analysis(monkeypatch, {"summary": "ok", "riskLevel": "low", "dates": {}})
        r = await _upload(client, uploader)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["aiAnalysisStatus"] == "success"
        assert body["aiAnalysisError"] is None

    async def test_exception_is_logged_not_shown(self, client, db, uploader, patched_pipeline, monkeypatch, caplog):
        _set_analysis(monkeypatch, RuntimeError(SECRET))
        with caplog.at_level(logging.ERROR, logger=contracts_route.logger.name):
            r = await _upload(client, uploader)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["aiAnalysisStatus"] == "failed"
        assert body["aiAnalysisError"] == contracts_route.ANALYSIS_FAILED_MESSAGE
        assert "secret-token" not in r.text and "pymongo" not in r.text

        stored = await db.contracts.find_one({"id": body["id"]})
        assert stored["aiAnalysisStatus"] == "failed"
        assert stored["aiAnalysisError"] == contracts_route.ANALYSIS_FAILED_MESSAGE
        # The detail still reaches the server log, with a traceback.
        assert any(SECRET in (rec.exc_text or "") or SECRET in rec.getMessage() for rec in caplog.records)
        assert any(rec.exc_info for rec in caplog.records)

    async def test_provider_unavailable_is_friendly(self, client, db, uploader, patched_pipeline, monkeypatch):
        _set_analysis(monkeypatch, None)
        body = (await _upload(client, uploader)).json()
        assert body["aiAnalysisStatus"] == "failed"
        assert body["aiAnalysisError"] == contracts_route.ANALYSIS_UNAVAILABLE_MESSAGE
        assert "detail page" not in body["aiAnalysisError"]

    async def test_parse_error_from_provider_is_friendly(self, client, db, uploader, patched_pipeline, monkeypatch):
        _set_analysis(monkeypatch, {"summary": "x", "riskLevel": "unknown", "error": "Could not parse structured analysis"})
        body = (await _upload(client, uploader)).json()
        assert body["aiAnalysisStatus"] == "failed"
        assert body["aiAnalysisError"] == contracts_route.ANALYSIS_FAILED_MESSAGE


class TestBulkUpload:
    async def _bulk(self, client, user, n=2):
        files = [("files", (f"c{i}.txt", b"Agreement text", "text/plain")) for i in range(n)]
        return await client.post(
            f"{API_PREFIX}/contracts/bulk",
            headers=auth_headers(user),
            files=files,
            data={"contractType": "General"},
        )

    async def test_analysis_exception_is_friendly(self, client, db, uploader, patched_pipeline, monkeypatch):
        _set_analysis(monkeypatch, RuntimeError(SECRET))
        r = await self._bulk(client, uploader)
        assert r.status_code == 200, r.text
        assert r.json()["successful"] == 2
        assert SECRET not in r.text
        async for doc in db.contracts.find({}):
            assert doc["aiAnalysisError"] == contracts_route.ANALYSIS_FAILED_MESSAGE

    async def test_unexpected_per_file_failure_is_friendly(
        self, client, db, uploader, patched_pipeline, monkeypatch, caplog
    ):
        _set_analysis(monkeypatch, {"summary": "ok", "riskLevel": "low", "dates": {}})

        async def _s3_boom(*args, **kwargs):
            raise ConnectionError(SECRET)

        monkeypatch.setattr(contracts_route, "upload_file_to_s3", _s3_boom)
        with caplog.at_level(logging.ERROR, logger=contracts_route.logger.name):
            r = await self._bulk(client, uploader)
        body = r.json()
        assert body["successful"] == 0 and body["failed"] == 2
        assert SECRET not in r.text
        assert {f["error"] for f in body["results"]["failed"]} == {contracts_route.BULK_FILE_FAILED_MESSAGE}
        # Quota reserved for each file was refunded.
        assert len(patched_pipeline["released"]) == 2
        assert any(rec.exc_info for rec in caplog.records)


class TestTrialCopy:
    @pytest.fixture
    def billing_db(self, db):
        billing_route.init_db(db)
        return db

    async def _expired_trial(self, db, org_id):
        await db.organization_billing.insert_one(
            {
                "organizationId": org_id,
                "subscriptionTier": "trial",
                "billingStatus": "trialing",
                "trialEndDate": (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(),
                "trialContractsUsed": 0,
                "trialContractsLimit": 3,
            }
        )

    async def test_reserve_message_has_no_trial_length(self, billing_db, org_id):
        await self._expired_trial(billing_db, org_id)
        ok, message = await billing_route.reserve_trial_upload_atomic(org_id)
        assert ok is False
        assert message == billing_route.TRIAL_ENDED_MESSAGE
        assert "day" not in message.lower()
        assert str(billing_route.TRIAL_DAYS) not in message

    async def test_check_access_message_has_no_trial_length(self, billing_db, org_id):
        await self._expired_trial(billing_db, org_id)
        ok, message = await billing_route.check_trial_access(org_id)
        assert ok is False
        assert message == billing_route.TRIAL_ENDED_MESSAGE

    async def test_plans_feature_list_has_no_trial_length(self):
        plans = await billing_route.get_plans()
        trial = next(p for p in plans["plans"] if p["id"] == "trial")
        assert not any("day" in f.lower() for f in trial["features"])
