"""Regression tests for the PR #92 review fixes (CodeRabbit + cto-new).

* failed single/bulk uploads remove their S3 object (all versions) and refund
  the trial slot, even if one rollback step raises;
* ``/billing/capabilities`` uses the same read-only states as the write gate,
  and an expired trial blocks uploads only;
* contract PATCH/restore: managers/admins always, users only on their own
  uploads, viewers never, other orgs 404;
* alert delivery attempts persist across scheduler runs, a live ``sending``
  claim is never re-claimed, failed alerts retry outside the expiry-date
  selection, and ``MAX_ATTEMPTS`` is a hard ceiling;
* manual check-and-send answers 202 and history includes legacy records;
* scheduler env parsing never breaks startup.
"""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import APIRouter, HTTPException
from httpx import ASGITransport, AsyncClient

from routes import alerts as alerts_route
from routes import billing as billing_route
from routes import contracts as contracts_route
from services import alert_scheduler, alert_service, storage_service
from utils import policy
from tests.unit.conftest import API_PREFIX, auth_headers, make_contract, make_user

UTC = timezone.utc


def _iso(days=0, **kw):
    return (datetime.now(UTC) + timedelta(days=days, **kw)).isoformat()


# --------------------------------------------------------------------------
# Upload rollback
# --------------------------------------------------------------------------
@pytest.fixture
def pipeline(monkeypatch, db):
    calls = {"released": [], "purged": [], "uploaded": []}

    async def _extract(content, mime):
        return "This Agreement is made between A and B."

    async def _reserve(org_id):
        return True, ""

    async def _release(org_id):
        calls["released"].append(org_id)

    async def _s3(content, filename, org_id, mime, extension=None):
        key = f"{org_id}/{len(calls['uploaded'])}-{filename}"
        calls["uploaded"].append(key)
        return key

    async def _purge(key):
        calls["purged"].append(key)
        return True

    async def _noop(**kwargs):
        return None

    async def _analyze(text):
        return {"summary": "ok"}

    monkeypatch.setattr(contracts_route, "extract_text_from_file", _extract)
    monkeypatch.setattr(contracts_route, "reserve_trial_upload_atomic", _reserve)
    monkeypatch.setattr(contracts_route, "release_trial_upload", _release)
    monkeypatch.setattr(contracts_route, "upload_file_to_s3", _s3)
    monkeypatch.setattr(contracts_route, "purge_uploaded_object", _purge)
    monkeypatch.setattr(contracts_route, "log_action", _noop)
    monkeypatch.setattr(contracts_route, "analyze_contract", _analyze)
    return calls


def _fail_contract_insert(monkeypatch, db):
    """Fail after the S3 write, before the contract record is persisted."""
    def _boom(*a, **k):
        raise RuntimeError("contract build failed")

    monkeypatch.setattr(contracts_route, "Contract", _boom)


class TestUploadRollback:
    async def test_bulk_failure_deletes_s3_object_and_refunds_slot(self, client, db, org_id, pipeline, monkeypatch):
        user = make_user(org_id, role="user")
        await db.users.insert_one(dict(user))
        _fail_contract_insert(monkeypatch, db)
        r = await client.post(
            f"{API_PREFIX}/contracts/bulk", headers=auth_headers(user),
            files=[("files", ("a.txt", b"This Agreement is made between A and B.", "text/plain"))],
        )
        assert r.status_code == 200, r.text
        assert r.json()["failed"] == 1
        assert pipeline["purged"] == pipeline["uploaded"] and len(pipeline["uploaded"]) == 1
        assert pipeline["released"] == [org_id]

    async def test_bulk_requires_upload_capability(self, client, db, org_id, pipeline):
        viewer = make_user(org_id, role="viewer")
        await db.users.insert_one(dict(viewer))
        r = await client.post(
            f"{API_PREFIX}/contracts/bulk", headers=auth_headers(viewer),
            files=[("files", ("a.txt", b"x", "text/plain"))],
        )
        assert r.status_code == 403
        assert pipeline["uploaded"] == []

    async def test_single_failure_deletes_s3_object_and_refunds_slot(self, client, db, org_id, pipeline, monkeypatch):
        user = make_user(org_id, role="user")
        await db.users.insert_one(dict(user))
        _fail_contract_insert(monkeypatch, db)
        r = await client.post(
            f"{API_PREFIX}/contracts", headers=auth_headers(user),
            files={"file": ("msa.txt", b"This Agreement is made between A and B.", "text/plain")},
            data={"title": "MSA", "contractType": "General"},
        )
        assert r.status_code >= 400
        assert pipeline["purged"] == pipeline["uploaded"] and len(pipeline["uploaded"]) == 1
        assert pipeline["released"] == [org_id]

    async def test_rollback_steps_are_isolated(self, db, org_id, monkeypatch):
        released = []

        async def _purge_boom(key):
            raise RuntimeError("s3 down")

        async def _release(oid):
            released.append(oid)

        async def _delete_boom(*a, **k):
            raise RuntimeError("db down")

        monkeypatch.setattr(contracts_route, "purge_uploaded_object", _purge_boom)
        monkeypatch.setattr(contracts_route, "release_trial_upload", _release)
        class _Contracts:
            delete_one = staticmethod(_delete_boom)

        class _DB:
            contracts = _Contracts()

        monkeypatch.setattr(contracts_route, "db", _DB())
        await contracts_route._rollback_failed_upload(org_id, "k", "c1")
        assert released == [org_id]


class _FakeS3:
    def __init__(self, listing=None, list_error=None):
        self.listing = listing or {}
        self.list_error = list_error
        self.deleted = []

    def list_object_versions(self, **kw):
        if self.list_error:
            raise self.list_error
        return self.listing

    def delete_object(self, **kw):
        self.deleted.append(kw)
        return {}


class TestPurgeUploadedObject:
    async def test_deletes_every_version_of_the_exact_key(self, monkeypatch):
        fake = _FakeS3({
            "Versions": [{"Key": "org/k.pdf", "VersionId": "v1"}, {"Key": "org/k.pdf.bak", "VersionId": "x"}],
            "DeleteMarkers": [{"Key": "org/k.pdf", "VersionId": "m1"}],
        })
        monkeypatch.setattr(storage_service, "get_s3_client", lambda: fake)
        monkeypatch.setattr(storage_service, "get_bucket_name", lambda: "b")
        assert await storage_service.purge_uploaded_object("org/k.pdf") is True
        assert [d.get("VersionId") for d in fake.deleted] == ["v1", "m1"]
        assert all(d["Key"] == "org/k.pdf" for d in fake.deleted)

    async def test_reports_failure_when_versions_cannot_be_listed(self, monkeypatch):
        from botocore.exceptions import ClientError
        err = ClientError({"Error": {"Code": "AccessDenied", "Message": "no"}}, "ListObjectVersions")
        fake = _FakeS3(list_error=err)
        monkeypatch.setattr(storage_service, "get_s3_client", lambda: fake)
        monkeypatch.setattr(storage_service, "get_bucket_name", lambda: "b")
        # Still tries the key delete, but must not claim the bytes are gone.
        assert await storage_service.purge_uploaded_object("org/k.pdf") is False
        assert fake.deleted == [{"Bucket": "b", "Key": "org/k.pdf"}]

    async def test_unversioned_bucket_uses_key_delete(self, monkeypatch):
        fake = _FakeS3({"Versions": [{"Key": "org/k.pdf", "VersionId": "null"}]})
        monkeypatch.setattr(storage_service, "get_s3_client", lambda: fake)
        monkeypatch.setattr(storage_service, "get_bucket_name", lambda: "b")
        await storage_service.purge_uploaded_object("org/k.pdf")
        assert fake.deleted == [{"Bucket": "b", "Key": "org/k.pdf"}]

    async def test_delete_handles_botocore_errors(self, monkeypatch):
        from botocore.exceptions import EndpointConnectionError

        class _Down(_FakeS3):
            def delete_object(self, **kw):
                raise EndpointConnectionError(endpoint_url="https://s3")

        monkeypatch.setattr(storage_service, "get_s3_client", lambda: _Down())
        monkeypatch.setattr(storage_service, "get_bucket_name", lambda: "b")
        assert await storage_service.delete_file_from_s3("k") is False


# --------------------------------------------------------------------------
# Billing capabilities
# --------------------------------------------------------------------------
@pytest.fixture
async def bclient(app):
    api = APIRouter(prefix=API_PREFIX)
    api.include_router(billing_route.router)
    app.include_router(api)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


class TestCapabilities:
    async def test_trial_expired_blocks_uploads_but_is_not_read_only(self, bclient, db, org_id):
        await db.organization_billing.insert_one({
            "id": "b1", "organizationId": org_id, "subscriptionTier": "trial", "billingStatus": "trialing",
            "trialStartDate": _iso(-20), "trialEndDate": _iso(-1)})
        admin = make_user(org_id, role="admin")
        await db.users.insert_one(dict(admin))
        r = await bclient.get(f"{API_PREFIX}/billing/capabilities", headers=auth_headers(admin))
        body = r.json()
        assert r.status_code == 200
        assert body["readOnly"] is False
        assert body["capabilities"]["canUpload"] is False
        assert body["capabilities"]["canApprove"] is True

    async def test_past_due_after_grace_is_read_only(self, bclient, db, org_id):
        from tests.unit.test_billing_port import paid
        await db.organization_billing.insert_one(
            {"id": "b1", "organizationId": org_id, **paid(billingStatus="past_due", pastDueSince=_iso(-9))})
        admin = make_user(org_id, role="admin")
        await db.users.insert_one(dict(admin))
        body = (await bclient.get(f"{API_PREFIX}/billing/capabilities", headers=auth_headers(admin))).json()
        assert body["readOnly"] is True
        assert not any(v for k, v in body["capabilities"].items() if k != "canViewAudit")

    def test_read_only_states_match_write_gate(self):
        from utils import billing_access as ba
        assert ba.ACCESS_TRIAL_EXPIRED not in ba.READ_ONLY_STATES
        assert {ba.ACCESS_PAST_DUE, ba.ACCESS_CANCELLED} <= ba.READ_ONLY_STATES


# --------------------------------------------------------------------------
# Contract write policy
# --------------------------------------------------------------------------
class TestRequireContractWrite:
    ORG = "org-1"

    def _user(self, role, sub="u1", org=None):
        return {"sub": sub, "role": role, "organizationId": org or self.ORG}

    def _contract(self, uploaded_by="u1"):
        return {"id": "c1", "organizationId": self.ORG, "uploadedBy": uploaded_by}

    @pytest.mark.parametrize("role", ["admin", "manager"])
    @pytest.mark.parametrize("cap", ["contract_edit", "restore"])
    def test_privileged_roles_can_write_any_contract(self, role, cap):
        policy.require_contract_write(self._user(role), self._contract("someone-else"), cap)

    @pytest.mark.parametrize("cap", ["contract_edit", "restore"])
    def test_user_can_write_own_contract_only(self, cap):
        policy.require_contract_write(self._user("user"), self._contract("u1"), cap)
        with pytest.raises(HTTPException) as exc:
            policy.require_contract_write(self._user("user"), self._contract("u2"), cap)
        assert exc.value.status_code == 403

    def test_viewer_is_denied_even_on_own_upload(self):
        with pytest.raises(HTTPException) as exc:
            policy.require_contract_write(self._user("viewer"), self._contract("u1"), "contract_edit")
        assert exc.value.status_code == 403

    def test_other_org_is_404(self):
        with pytest.raises(HTTPException) as exc:
            policy.require_contract_write(self._user("admin", org="org-2"), self._contract(), "contract_edit")
        assert exc.value.status_code == 404

    def test_capability_map_own_flags(self):
        caps = policy.capability_map("user")
        assert caps["canEditOwnContract"] and caps["canRestoreOwn"]
        assert not caps["canEditContract"] and not caps["canRestore"] and not caps["canDelete"]
        assert policy.capability_map("viewer")["canEditOwnContract"] is False
        assert policy.capability_map("admin", read_only=True)["canRestoreOwn"] is False


# --------------------------------------------------------------------------
# Alerts
# --------------------------------------------------------------------------
@pytest.fixture
def sends(monkeypatch):
    log = {"calls": 0, "results": []}

    async def _send(**kw):
        log["calls"] += 1
        return log["results"].pop(0) if log["results"] else {"status": "error"}

    monkeypatch.setattr(alert_service, "send_expiration_alert", _send)
    return log


async def _seed_alert_org(db, org_id, days=7):
    # Production relies on the unique idempotency index (utils/database.py)
    # to turn an upsert against a non-claimable record into DuplicateKeyError.
    await db.expiration_alerts.create_index("idempotencyKey", unique=True, sparse=True, name="uq_alerts_idempotency")
    admin = make_user(org_id, role="admin")
    await db.users.insert_one(dict(admin))
    target = (datetime.now(UTC) + timedelta(days=days)).strftime("%Y-%m-%d")
    contract = make_contract(org_id, admin["id"], expiryDate=target, status="active")
    await db.contracts.insert_one(dict(contract))
    await db.alert_settings.insert_one({"organizationId": org_id, "emailEnabled": True, "alertDays": [days]})
    return admin, contract


async def _make_due(db):
    await db.expiration_alerts.update_many({"status": "failed"}, {"$set": {"nextAttemptAt": _iso(minutes=-1)}})


class TestAlertDelivery:
    async def test_attempts_persist_across_runs_and_cap_at_max(self, db, org_id, sends):
        await _seed_alert_org(db, org_id)
        await alert_service.scan_expiration_alerts(db, org_id)
        rec = await db.expiration_alerts.find_one({})
        assert rec["attempts"] == 1 and rec["status"] == "failed" and rec["nextAttemptAt"]
        assert rec["organizationId"] == org_id and rec["userId"]

        for _ in range(5):  # many scheduler runs
            await _make_due(db)
            await alert_service.scan_expiration_alerts(db, org_id)
        rec = await db.expiration_alerts.find_one({})
        assert sends["calls"] == alert_service.MAX_ATTEMPTS
        assert rec["attempts"] == alert_service.MAX_ATTEMPTS
        assert rec["failureReason"] == "max_attempts_exhausted"

    async def test_failed_alert_not_retried_before_backoff(self, db, org_id, sends):
        await _seed_alert_org(db, org_id)
        await alert_service.scan_expiration_alerts(db, org_id)
        await alert_service.scan_expiration_alerts(db, org_id)
        assert sends["calls"] == 1

    async def test_success_is_sent_once(self, db, org_id, sends):
        await _seed_alert_org(db, org_id)
        sends["results"] = [{"status": "success", "email_id": "m1"}]
        await alert_service.scan_expiration_alerts(db, org_id)
        await alert_service.scan_expiration_alerts(db, org_id)
        rec = await db.expiration_alerts.find_one({})
        assert sends["calls"] == 1 and rec["status"] == "sent" and rec["attempts"] == 1

    async def test_live_sending_claim_is_not_reclaimed(self, db, org_id, sends):
        admin, contract = await _seed_alert_org(db, org_id)
        key = f"{org_id}:{contract['id']}:expiration:7:{admin['id']}"
        await db.expiration_alerts.insert_one({
            "idempotencyKey": key, "organizationId": org_id, "contractId": contract["id"], "userId": admin["id"],
            "daysBeforeExpiry": 7, "status": "sending", "claimToken": "other", "claimedAt": _iso(), "attempts": 1})
        await alert_service.scan_expiration_alerts(db, org_id)
        assert sends["calls"] == 0

    async def test_expired_lease_is_reclaimed(self, db, org_id, sends):
        admin, contract = await _seed_alert_org(db, org_id)
        key = f"{org_id}:{contract['id']}:expiration:7:{admin['id']}"
        await db.expiration_alerts.insert_one({
            "idempotencyKey": key, "organizationId": org_id, "contractId": contract["id"], "userId": admin["id"],
            "daysBeforeExpiry": 7, "status": "sending", "claimToken": "dead",
            "claimedAt": _iso(minutes=-30), "attempts": 1})
        sends["results"] = [{"status": "success"}]
        await alert_service.scan_expiration_alerts(db, org_id)
        rec = await db.expiration_alerts.find_one({})
        assert sends["calls"] == 1 and rec["status"] == "sent" and rec["attempts"] == 2

    async def test_retry_pass_runs_outside_expiry_date_selection(self, db, org_id, sends):
        admin, contract = await _seed_alert_org(db, org_id)
        await alert_service.scan_expiration_alerts(db, org_id)
        # Next day: the contract no longer matches the exact 7-day window.
        await db.contracts.update_one({"id": contract["id"]}, {"$set": {
            "expiryDate": (datetime.now(UTC) + timedelta(days=6)).strftime("%Y-%m-%d")}})
        await _make_due(db)
        sends["results"] = [{"status": "success"}]
        counts = await alert_service.retry_failed_alerts(db)
        rec = await db.expiration_alerts.find_one({})
        assert counts["sent"] == 1 and rec["status"] == "sent" and rec["attempts"] == 2

    async def test_retry_skips_ineligible_recipient(self, db, org_id, sends):
        admin, _ = await _seed_alert_org(db, org_id)
        await alert_service.scan_expiration_alerts(db, org_id)
        await db.users.update_one({"id": admin["id"]}, {"$set": {"role": "viewer"}})
        await _make_due(db)
        await alert_service.retry_failed_alerts(db)
        rec = await db.expiration_alerts.find_one({})
        assert sends["calls"] == 1 and rec["status"] == "skipped"

    async def test_provider_exception_is_recorded_as_failed(self, db, org_id, monkeypatch):
        async def _raise(**kw):
            raise RuntimeError("provider exploded")

        monkeypatch.setattr(alert_service, "send_expiration_alert", _raise)
        await _seed_alert_org(db, org_id)
        await alert_service.scan_expiration_alerts(db, org_id)
        rec = await db.expiration_alerts.find_one({})
        assert rec["status"] == "failed" and rec["attempts"] == 1


@pytest.fixture
async def aclient(app, db):
    alerts_route.init_db(db)
    api = APIRouter(prefix=API_PREFIX)
    api.include_router(alerts_route.router)
    app.include_router(api)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


class TestAlertRoutes:
    async def test_check_and_send_returns_202_and_runs_in_background(self, aclient, db, org_id, monkeypatch):
        ran = []

        async def _scan(database, org, actor):
            ran.append(org)
            return {}

        async def _writable(org):
            return None

        monkeypatch.setattr(alerts_route, "scan_expiration_alerts", _scan)
        monkeypatch.setattr(alerts_route, "assert_org_writable", _writable)
        admin = make_user(org_id, role="admin")
        await db.users.insert_one(dict(admin))
        r = await aclient.post(f"{API_PREFIX}/alerts/check-and-send", headers=auth_headers(admin))
        assert r.status_code == 202
        assert r.json() == {"message": "Alert check started", "status": "accepted"}
        assert ran == [org_id]

    async def test_check_and_send_denied_for_user_role(self, aclient, db, org_id):
        user = make_user(org_id, role="user")
        await db.users.insert_one(dict(user))
        r = await aclient.post(f"{API_PREFIX}/alerts/check-and-send", headers=auth_headers(user))
        assert r.status_code == 403

    async def test_history_includes_legacy_records_without_org(self, aclient, db, org_id, other_org_id):
        admin = make_user(org_id, role="admin")
        await db.users.insert_one(dict(admin))
        mine = make_contract(org_id, admin["id"])
        theirs = make_contract(other_org_id, "x")
        await db.contracts.insert_many([dict(mine), dict(theirs)])
        await db.expiration_alerts.insert_many([
            {"id": "legacy", "contractId": mine["id"], "createdAt": _iso(-2)},
            {"id": "new", "contractId": mine["id"], "organizationId": org_id, "createdAt": _iso(-1)},
            {"id": "foreign", "contractId": theirs["id"], "createdAt": _iso(-1)},
        ])
        r = await aclient.get(f"{API_PREFIX}/alerts/history", headers=auth_headers(admin))
        ids = {a["id"] for a in r.json()["alerts"]}
        assert ids == {"legacy", "new"}


class TestSchedulerEnv:
    @pytest.mark.parametrize("raw,expected", [(None, 8), ("", 8), ("  ", 8), ("abc", 8), ("12", 12), ("99", 23), ("-3", 0)])
    def test_env_int_is_defensive(self, monkeypatch, raw, expected):
        if raw is None:
            monkeypatch.delenv("ALERT_SCAN_UTC_HOUR", raising=False)
        else:
            monkeypatch.setenv("ALERT_SCAN_UTC_HOUR", raw)
        assert alert_scheduler._env_int("ALERT_SCAN_UTC_HOUR", 8, 0, 23) == expected

    def test_bad_env_does_not_break_scheduler_and_retry_job_exists(self, monkeypatch, db):
        monkeypatch.setenv("ALERT_SCAN_UTC_HOUR", "eight")
        monkeypatch.setenv("ALERT_SCAN_UTC_MINUTE", "x")
        scheduler = alert_scheduler.build_alert_scheduler(db)
        ids = {job.id for job in scheduler.get_jobs()}
        assert {"daily-expiration-renewal-alerts", "expiration-alert-retries"} <= ids


class TestRestoreTransitionCapability:
    async def _seed(self, db, org_id, role, current, restored):
        user = make_user(org_id, role=role)
        await db.users.insert_one(dict(user))
        contract = make_contract(org_id, user["id"], status=current)
        await db.contracts.insert_one(dict(contract))
        await db.contract_versions.insert_one({
            "contractId": contract["id"], "version": 1, "title": contract["title"],
            "contractType": contract.get("contractType", "General"), "status": restored})
        return user, contract

    async def test_user_cannot_approve_own_contract_via_restore(self, client, db, org_id):
        user, contract = await self._seed(db, org_id, "user", "review", "approved")
        r = await client.post(f"{API_PREFIX}/contracts/{contract['id']}/restore/1", headers=auth_headers(user))
        assert r.status_code == 403
        assert (await db.contracts.find_one({"id": contract["id"]}))["status"] == "review"

    async def test_user_can_submit_own_contract_via_restore(self, client, db, org_id):
        user, contract = await self._seed(db, org_id, "user", "draft", "review")
        r = await client.post(f"{API_PREFIX}/contracts/{contract['id']}/restore/1", headers=auth_headers(user))
        assert r.status_code == 200, r.text

    async def test_manager_can_approve_via_restore(self, client, db, org_id):
        mgr, contract = await self._seed(db, org_id, "manager", "review", "approved")
        r = await client.post(f"{API_PREFIX}/contracts/{contract['id']}/restore/1", headers=auth_headers(mgr))
        assert r.status_code == 200, r.text

    def test_every_workflow_transition_has_a_capability(self):
        from services.workflow_service import WORKFLOW_TRANSITIONS
        pairs = {(a, b) for a, targets in WORKFLOW_TRANSITIONS.items() for b in targets}
        assert pairs == set(contracts_route.RESTORE_TRANSITION_CAPABILITY)


class TestClaimTimestamp:
    async def test_claimed_at_is_claim_time_not_scan_start(self, db, org_id, monkeypatch):
        times = iter([datetime(2026, 1, 1, tzinfo=UTC)] + [datetime(2026, 1, 1, 0, 20, tzinfo=UTC)] * 50)
        monkeypatch.setattr(alert_service, "_now", lambda: next(times))
        update = alert_service._claim_update("t")
        assert update["$set"]["claimedAt"] == datetime(2026, 1, 1, tzinfo=UTC).isoformat()
        assert alert_service._claim_update("t")["$set"]["claimedAt"].startswith("2026-01-01T00:20")


class TestAlertReadinessGate:
    async def test_index_ready_detection(self, db):
        assert await alert_service.alert_idempotency_index_ready(db) is False
        await db.expiration_alerts.create_index("idempotencyKey", unique=True, sparse=True, name="uq_alerts_idempotency")
        assert await alert_service.alert_idempotency_index_ready(db) is True

    async def test_scheduler_jobs_skip_until_ready(self, db, monkeypatch):
        calls = []

        async def _scan(database):
            calls.append("scan")
            return {}

        async def _retry(database):
            calls.append("retry")
            return {}

        monkeypatch.setattr(alert_scheduler, "scan_expiration_alerts", _scan)
        monkeypatch.setattr(alert_scheduler, "retry_failed_alerts", _retry)
        ready = {"ok": False}
        scheduler = alert_scheduler.build_alert_scheduler(db, is_ready=lambda: ready["ok"])
        jobs = {job.id: job.func for job in scheduler.get_jobs()}
        await jobs["daily-expiration-renewal-alerts"]()
        await jobs["expiration-alert-retries"]()
        assert calls == []
        ready["ok"] = True
        await jobs["daily-expiration-renewal-alerts"]()
        await jobs["expiration-alert-retries"]()
        assert calls == ["scan", "retry"]

    async def test_manual_check_is_503_until_ready(self, aclient, app, db, org_id, monkeypatch):
        async def _writable(org):
            return None

        monkeypatch.setattr(alerts_route, "assert_org_writable", _writable)
        app.state.alerts_ready = False
        admin = make_user(org_id, role="admin")
        await db.users.insert_one(dict(admin))
        r = await aclient.post(f"{API_PREFIX}/alerts/check-and-send", headers=auth_headers(admin))
        assert r.status_code == 503


class TestUploadVerificationCleanup:
    async def test_failed_verification_purges_every_version(self, monkeypatch):
        class _S3(_FakeS3):
            def put_object(self, **kw):
                return {"VersionId": "v1"}

            def head_object(self, **kw):
                return {"ContentLength": 1}  # wrong length -> verification fails

            def list_object_versions(self, **kw):
                return {"Versions": [{"Key": kw["Prefix"], "VersionId": "v1"}]}

        fake = _S3()
        purged = []

        async def _purge(key):
            purged.append(key)
            return await real_purge(key)

        real_purge = storage_service.purge_uploaded_object
        monkeypatch.setattr(storage_service, "get_s3_client", lambda: fake)
        monkeypatch.setattr(storage_service, "get_bucket_name", lambda: "b")
        monkeypatch.setattr(storage_service, "purge_uploaded_object", _purge)
        with pytest.raises(ValueError):
            await storage_service.upload_file_to_s3(b"hello", "c.pdf", "org", "application/pdf")
        assert len(purged) == 1 and purged[0].startswith("org/")
        # Version-aware delete, never a bare key delete on a versioned bucket.
        assert fake.deleted == [{"Bucket": "b", "Key": purged[0], "VersionId": "v1"}]

    async def test_purge_exception_does_not_mask_upload_error(self, monkeypatch):
        class _S3(_FakeS3):
            def put_object(self, **kw):
                from botocore.exceptions import EndpointConnectionError
                raise EndpointConnectionError(endpoint_url="https://s3")

        async def _boom(key):
            raise RuntimeError("purge down")

        monkeypatch.setattr(storage_service, "get_s3_client", lambda: _S3())
        monkeypatch.setattr(storage_service, "get_bucket_name", lambda: "b")
        monkeypatch.setattr(storage_service, "purge_uploaded_object", _boom)
        with pytest.raises(ValueError, match="Durable storage verification failed"):
            await storage_service.upload_file_to_s3(b"x", "c.pdf", "org", "application/pdf")


class TestApprovedToReviewRestore:
    async def test_user_can_send_own_approved_contract_back_to_review(self, client, db, org_id):
        user, contract = await TestRestoreTransitionCapability()._seed(db, org_id, "user", "approved", "review")
        r = await client.post(f"{API_PREFIX}/contracts/{contract['id']}/restore/1", headers=auth_headers(user))
        assert r.status_code == 200, r.text
        assert (await db.contracts.find_one({"id": contract["id"]}))["status"] == "review"

    def test_mapping_matches_workflow_action(self):
        assert contracts_route.RESTORE_TRANSITION_CAPABILITY[("approved", "review")] == "submit_for_review"


class TestAlertIndexRecovery:
    async def test_retries_until_index_verified_then_enables(self, db, monkeypatch):
        attempts = {"n": 0}
        real = db.expiration_alerts

        class _Coll:
            async def create_index(self, *a, **kw):
                attempts["n"] += 1
                if attempts["n"] < 3:
                    raise RuntimeError("mongo blip")
                return await real.create_index(*a, **kw)

            async def index_information(self):
                return await real.index_information()

        class _DB:
            expiration_alerts = _Coll()

        sleeps, enabled = [], []

        async def _sleep(d):
            assert not enabled  # never enabled before verification
            sleeps.append(d)

        await alert_service.ensure_alert_idempotency_index(
            _DB(), lambda: enabled.append(True), initial_delay=1, max_delay=2, sleep=_sleep)
        assert enabled == [True] and attempts["n"] == 3 and sleeps == [1, 2]
        assert await alert_service.alert_idempotency_index_ready(db)
