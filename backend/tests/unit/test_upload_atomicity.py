"""Upload atomicity: the quota slot is refunded and the stored object purged
only when the contract insert did not commit, exactly once. Once the record
is saved, audit or lookup failures are logged and never fail or roll back the
upload. An insert whose outcome cannot be established keeps both the object
and the slot."""

import pytest
from pymongo.errors import NetworkTimeout, WriteError

from routes import contracts as contracts_route
from tests.unit.conftest import API_PREFIX, auth_headers, make_user

DOC = b"This Agreement is made between A and B."


@pytest.fixture
def pipeline(monkeypatch):
    """Stub storage, quota, extraction, analysis and audit; record side effects."""
    calls = {"released": [], "purged": [], "uploaded": []}

    async def _extract(content, mime):
        return DOC.decode()

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


@pytest.fixture
async def user(db, org_id):
    """A persisted uploader in the test org."""
    u = make_user(org_id, role="user")
    await db.users.insert_one(dict(u))
    return u


def _patch_collection(monkeypatch, db, name, method, fake):
    """Replace ``method`` for collection ``name`` only, keeping the original."""
    cls = type(db.contracts)
    original = getattr(cls, method)

    async def wrapper(self, *args, **kwargs):
        if self.name == name:
            return await fake(original, self, *args, **kwargs)
        return await original(self, *args, **kwargs)

    monkeypatch.setattr(cls, method, wrapper)


def _rejected():
    return WriteError("Document failed validation", code=121)


def _ambiguous():
    return NetworkTimeout("timed out waiting for the insert reply")


def _fail_inserts(monkeypatch, db, *, commit=False, fail_on=None, error=_rejected):
    """Make contract inserts raise ``error()`` (by default a server rejection,
    which proves nothing was written); ``commit`` writes the record first (a
    retried write that committed), ``fail_on`` limits failures to those files."""

    async def fake(original, self, doc, *args, **kwargs):
        if fail_on is not None and doc.get("fileName") not in fail_on:
            return await original(self, doc, *args, **kwargs)
        if commit:
            await original(self, doc, *args, **kwargs)
        raise error()

    _patch_collection(monkeypatch, db, "contracts", "insert_one", fake)


async def _upload(client, user, name="msa.txt"):
    """POST one file to the single-upload endpoint."""
    return await client.post(
        f"{API_PREFIX}/contracts",
        headers=auth_headers(user),
        files={"file": (name, DOC, "text/plain")},
        data={"title": "MSA", "contractType": "General"},
    )


async def _bulk(client, user, names):
    """POST the named files to the bulk-upload endpoint."""
    return await client.post(
        f"{API_PREFIX}/contracts/bulk",
        headers=auth_headers(user),
        files=[("files", (n, DOC, "text/plain")) for n in names],
    )


class TestSingleUpload:
    """Rollback and follow-up behaviour of ``POST /contracts``."""

    async def test_storage_ok_insert_fails_purges_and_refunds_once(
        self, client, db, user, pipeline, monkeypatch
    ):
        """Storage write succeeded, insert did not commit: purge and refund once."""
        _fail_inserts(monkeypatch, db)
        r = await _upload(client, user)
        assert r.status_code == 500
        assert "Nothing was saved" in r.json()["detail"]
        assert pipeline["purged"] == pipeline["uploaded"] and pipeline["uploaded"]
        assert pipeline["released"] == [user["organizationId"]]
        assert await db.contracts.count_documents({}) == 0

    async def test_insert_that_committed_despite_error_is_kept(
        self, client, db, user, pipeline, monkeypatch
    ):
        """An insert that committed but raised is treated as saved."""
        _fail_inserts(monkeypatch, db, commit=True, error=_ambiguous)
        r = await _upload(client, user)
        assert r.status_code == 200, r.text
        assert await db.contracts.count_documents({"id": r.json()["id"]}) == 1
        assert pipeline["purged"] == [] and pipeline["released"] == []

    async def test_unverifiable_insert_keeps_object_and_slot(
        self, client, db, user, pipeline, monkeypatch
    ):
        """An unknown insert outcome keeps the stored object and the slot."""
        _fail_inserts(monkeypatch, db, error=_ambiguous)

        async def down(original, self, *args, **kwargs):
            raise RuntimeError("primary unavailable")

        _patch_collection(monkeypatch, db, "contracts", "find_one", down)
        r = await _upload(client, user)
        assert r.status_code == 500
        assert r.json()["detail"] == contracts_route.UPLOAD_UNCONFIRMED_MESSAGE
        assert pipeline["purged"] == [] and pipeline["released"] == []

    async def test_audit_failure_does_not_fail_or_roll_back(
        self, client, db, user, pipeline, monkeypatch, caplog
    ):
        """An audit failure after the insert is logged only."""

        async def audit_down(**kwargs):
            raise RuntimeError("audit store down")

        monkeypatch.setattr(contracts_route, "log_action", audit_down)
        r = await _upload(client, user)
        assert r.status_code == 200, r.text
        assert await db.contracts.count_documents({"id": r.json()["id"]}) == 1
        assert pipeline["purged"] == [] and pipeline["released"] == []
        assert "Audit log for uploaded contract" in caplog.text

    async def test_post_insert_error_does_not_fail_or_roll_back(
        self, client, db, user, pipeline, monkeypatch
    ):
        """A post-insert lookup error does not undo the saved upload."""

        async def lookup_down(original, self, flt=None, *args, **kwargs):
            projection = args[0] if args else kwargs.get("projection")
            if projection == {"_id": 0, "email": 1}:
                raise RuntimeError("users lookup failed")
            return await original(self, flt, *args, **kwargs)

        _patch_collection(monkeypatch, db, "users", "find_one", lookup_down)
        r = await _upload(client, user)
        assert r.status_code == 200, r.text
        assert r.json()["uploaderEmail"] is None
        assert await db.contracts.count_documents({"id": r.json()["id"]}) == 1
        assert pipeline["purged"] == [] and pipeline["released"] == []

    async def test_ambiguous_error_with_no_record_is_unconfirmed(
        self, client, db, user, pipeline, monkeypatch
    ):
        """A timeout may hide an insert still in flight: an empty lookup is not
        proof, so the object and slot are kept and the case is flagged."""
        _fail_inserts(monkeypatch, db, error=_ambiguous)
        r = await _upload(client, user)
        assert r.status_code == 500
        assert r.json()["detail"] == contracts_route.UPLOAD_UNCONFIRMED_MESSAGE
        assert pipeline["purged"] == [] and pipeline["released"] == []

    async def test_write_concern_error_with_no_record_is_unconfirmed(
        self, client, db, user, pipeline, monkeypatch
    ):
        """A write-concern error may follow an applied write: not a rejection."""
        from pymongo.errors import WriteConcernError

        _fail_inserts(
            monkeypatch, db, error=lambda: WriteConcernError("wtimeout", code=64)
        )
        r = await _upload(client, user)
        assert r.status_code == 500
        assert r.json()["detail"] == contracts_route.UPLOAD_UNCONFIRMED_MESSAGE
        assert pipeline["purged"] == [] and pipeline["released"] == []

    async def test_duplicate_key_without_our_record_is_a_failure(
        self, client, db, user, pipeline, monkeypatch
    ):
        """A unique-index rejection with our record absent means not written."""
        from pymongo.errors import DuplicateKeyError

        _fail_inserts(
            monkeypatch, db, error=lambda: DuplicateKeyError("E11000", code=11000)
        )
        r = await _upload(client, user)
        assert r.status_code == 500
        assert "Nothing was saved" in r.json()["detail"]
        assert pipeline["purged"] == pipeline["uploaded"]
        assert pipeline["released"] == [user["organizationId"]]


class TestBulkUpload:
    """Per-file rollback behaviour of ``POST /contracts/bulk``."""

    async def test_partial_failure_rolls_back_only_the_failed_file(
        self, client, db, user, pipeline, monkeypatch
    ):
        """Only the file whose insert failed is purged and refunded."""
        _fail_inserts(monkeypatch, db, fail_on={"b.txt"})
        r = await _bulk(client, user, ["a.txt", "b.txt", "c.txt"])
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["successful"] == 2 and body["failed"] == 1
        assert body["results"]["failed"][0]["filename"] == "b.txt"
        assert pipeline["purged"] == [pipeline["uploaded"][1]]
        assert pipeline["released"] == [user["organizationId"]]
        names = sorted(d["fileName"] for d in await db.contracts.find({}).to_list(10))
        assert names == ["a.txt", "c.txt"]

    async def test_unverifiable_insert_is_reported_without_rollback(
        self, client, db, user, pipeline, monkeypatch
    ):
        """An unknown outcome is reported per file and nothing is rolled back."""
        _fail_inserts(monkeypatch, db, error=_ambiguous)

        async def down(original, self, *args, **kwargs):
            raise RuntimeError("primary unavailable")

        _patch_collection(monkeypatch, db, "contracts", "find_one", down)
        r = await _bulk(client, user, ["a.txt"])
        body = r.json()
        assert body["failed"] == 1
        assert (
            body["results"]["failed"][0]["error"]
            == contracts_route.UPLOAD_UNCONFIRMED_MESSAGE
        )
        assert pipeline["purged"] == [] and pipeline["released"] == []

    async def test_audit_failure_keeps_every_saved_file(
        self, client, db, user, pipeline, monkeypatch
    ):
        """Audit failures never fail a saved bulk file."""

        async def audit_down(**kwargs):
            raise RuntimeError("audit store down")

        monkeypatch.setattr(contracts_route, "log_action", audit_down)
        r = await _bulk(client, user, ["a.txt", "b.txt"])
        assert r.json()["successful"] == 2
        assert pipeline["purged"] == [] and pipeline["released"] == []


@pytest.mark.parametrize("bulk", [False, True])
async def test_confirmed_failure_passes_contract_id_to_org_scoped_delete(
    client, db, user, pipeline, monkeypatch, bulk
):
    """Single and bulk rollbacks pass the contract id, so the org-scoped delete
    can remove a write the server applies after the insert call failed."""
    deleted = []
    _fail_inserts(monkeypatch, db)

    async def spy_delete(original, self, flt, *args, **kwargs):
        deleted.append(flt)
        return await original(self, flt, *args, **kwargs)

    _patch_collection(monkeypatch, db, "contracts", "delete_one", spy_delete)
    if bulk:
        r = await _bulk(client, user, ["a.txt"])
        assert r.json()["failed"] == 1
    else:
        r = await _upload(client, user)
        assert r.status_code == 500
    assert len(deleted) == 1
    assert deleted[0]["organizationId"] == user["organizationId"]
    assert deleted[0]["id"]


async def test_bulk_cancellation_during_insert_does_not_roll_back(
    client, db, user, pipeline, monkeypatch
):
    """A cancellation while the insert is in flight leaves the outcome unknown,
    so the bulk path keeps the object and the slot."""
    import asyncio

    async def cancelled(original, self, doc, *args, **kwargs):
        raise asyncio.CancelledError()

    _patch_collection(monkeypatch, db, "contracts", "insert_one", cancelled)
    with pytest.raises(asyncio.CancelledError):
        await contracts_route.bulk_upload_contracts(
            files=[_FakeUpload("a.txt")],
            contractType="General",
            current_user={**user, "sub": user["id"]},
        )
    assert pipeline["purged"] == [] and pipeline["released"] == []


class _FakeUpload:
    """Minimal UploadFile stand-in for calling the route function directly."""

    def __init__(self, filename):
        self.filename = filename

    async def read(self):
        return DOC
