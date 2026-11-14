"""Redis is optional for rate limiting, invitation limits are shared, and
uploads over ``MAX_UPLOAD_MB`` are refused with 413 before quota or storage."""

import logging

import pytest
import redis.asyncio as aioredis

from routes import contracts as contracts_route
from tests.unit.conftest import API_PREFIX, auth_headers, make_user
from utils import rbac
from utils import upload_limits
from utils.upload_limits import UploadSizeLimitMiddleware

PREVIEW_URL = f"{API_PREFIX}/team/accept-invite/preview"
ACCEPT_URL = f"{API_PREFIX}/team/accept-invite"
MB = 1024 * 1024


# ---------------------------------------------------------------------------
# A tiny in-memory stand-in for redis.asyncio (INCR + EXPIRE pipelines only)
# ---------------------------------------------------------------------------
class _FakePipeline:
    def __init__(self, store):
        self.store = store
        self.ops = []

    def incr(self, key):
        self.ops.append(("incr", key))

    def expire(self, key, seconds):
        self.ops.append(("expire", key))

    async def execute(self):
        if self.store.down:
            raise ConnectionError("redis is down")
        out = []
        for op, key in self.ops:
            if op == "incr":
                self.store.counts[key] = self.store.counts.get(key, 0) + 1
                out.append(self.store.counts[key])
            else:
                out.append(True)
        return out


class _FakeRedis:
    def __init__(self):
        self.counts = {}
        self.down = False
        self.urls = []

    def from_url(self, url, **kwargs):
        self.urls.append(url)
        return self

    def pipeline(self):
        return _FakePipeline(self)


@pytest.fixture
def no_redis(monkeypatch):
    for name in ("REDIS_URL", "RATE_LIMIT_REDIS_URL"):
        monkeypatch.delenv(name, raising=False)
    rbac.reset_shared_ip_limiters()
    yield
    rbac.reset_shared_ip_limiters()


@pytest.fixture
def fake_redis(monkeypatch):
    fake = _FakeRedis()
    monkeypatch.delenv("RATE_LIMIT_REDIS_URL", raising=False)
    monkeypatch.setenv("REDIS_URL", "redis://redis.internal:6379/0")
    monkeypatch.setattr(aioredis, "from_url", fake.from_url)
    rbac.reset_shared_ip_limiters()
    yield fake
    rbac.reset_shared_ip_limiters()


# ---------------------------------------------------------------------------
# Backend selection and the startup warning
# ---------------------------------------------------------------------------
class TestBackendSelection:
    def test_no_redis_is_in_memory(self, no_redis):
        assert rbac.rate_limit_backend() == "memory"
        assert isinstance(
            rbac.make_org_limiter(5, 60, "t", fail_closed=True),
            rbac._InMemoryAsyncLimiter,
        )

    def test_rate_limit_url_wins_over_redis_url(self, monkeypatch, fake_redis):
        monkeypatch.setenv("RATE_LIMIT_REDIS_URL", "rediss://limits:6380/1")
        assert rbac.rate_limit_redis_url() == "rediss://limits:6380/1"
        assert isinstance(rbac.make_org_limiter(5, 60, "t"), rbac.RedisRateLimiter)
        assert fake_redis.urls == ["rediss://limits:6380/1"]

    def test_non_redis_url_is_in_memory(self, monkeypatch, no_redis):
        monkeypatch.setenv("REDIS_URL", "http://not-redis")
        assert rbac.rate_limit_backend() == "invalid"
        assert isinstance(rbac.make_org_limiter(5, 60, "t"), rbac._InMemoryAsyncLimiter)

    async def test_broken_client_never_raises_at_build_time(self, monkeypatch):
        monkeypatch.setenv("REDIS_URL", "redis://redis.internal:6379/0")

        def boom(*args, **kwargs):
            raise ValueError("bad url")

        monkeypatch.setattr(aioredis, "from_url", boom)
        open_limiter = rbac.make_org_limiter(5, 60, "t")
        assert isinstance(open_limiter, rbac._InMemoryAsyncLimiter)
        assert (await open_limiter.acheck("k"))[0] is True
        closed = rbac.make_org_limiter(5, 60, "t", fail_closed=True)
        with pytest.raises(rbac.RateLimiterUnavailable):
            await closed.acheck("k")

    @pytest.mark.parametrize(
        "env,level,text",
        [
            ({}, logging.WARNING, "Redis is not configured"),
            ({"REDIS_URL": "http://x"}, logging.WARNING, "not a redis://"),
            ({"REDIS_URL": "redis://r:6379/0"}, logging.INFO, "through Redis"),
        ],
    )
    def test_startup_log_is_written_once(
        self, monkeypatch, caplog, no_redis, env, level, text
    ):
        for name, value in env.items():
            monkeypatch.setenv(name, value)
        monkeypatch.setattr(rbac, "_backend_logged", False)
        with caplog.at_level(logging.INFO, logger="utils.rbac"):
            rbac.log_rate_limit_backend()
            rbac.log_rate_limit_backend()
        hits = [r for r in caplog.records if text in r.getMessage()]
        assert len(hits) == 1 and hits[0].levelno == level


# ---------------------------------------------------------------------------
# Bulk limiters: fail closed only when Redis is configured and unreachable
# ---------------------------------------------------------------------------
class TestFailClosed:
    async def test_without_redis_bulk_limiter_counts_in_process(self, no_redis):
        limiter = rbac.make_org_limiter(2, 60, "bulk", fail_closed=True)
        assert [(await limiter.acheck("org"))[0] for _ in range(3)] == [
            True,
            True,
            False,
        ]

    async def test_configured_but_down_fails_closed(self, fake_redis):
        fake_redis.down = True
        limiter = rbac.make_org_limiter(2, 60, "bulk", fail_closed=True)
        with pytest.raises(rbac.RateLimiterUnavailable):
            await limiter.acheck("org")

    async def test_configured_but_down_fails_open_for_normal_limits(self, fake_redis):
        fake_redis.down = True
        limiter = rbac.make_org_limiter(2, 60, "chat")
        assert [(await limiter.acheck("org"))[0] for _ in range(3)] == [
            True,
            True,
            False,
        ]


# ---------------------------------------------------------------------------
# Invitation limits go through the shared limiter
# ---------------------------------------------------------------------------
class TestInvitationLimits:
    async def test_in_process_fallback_without_redis(self, client, no_redis):
        codes = [
            (await client.post(PREVIEW_URL, json={"token": f"g-{i}"})).status_code
            for i in range(11)
        ]
        assert 429 not in codes[:10]
        assert codes[10] == 429

    async def test_counts_are_shared_across_workers_through_redis(
        self, client, fake_redis
    ):
        codes = []
        for i in range(11):
            # A fresh worker every request: only Redis remembers the attempts.
            rbac.INVITE_RATE_LIMITER.reset()
            codes.append(
                (await client.post(PREVIEW_URL, json={"token": f"g-{i}"})).status_code
            )
        assert 429 not in codes[:10]
        assert codes[10] == 429
        assert any(k.startswith("invite-ip:") for k in fake_redis.counts)

    async def test_accept_is_limited_too(self, client, fake_redis):
        body = {"token": "nope", "name": "N", "password": "Str0ng!pass"}
        codes = []
        for _ in range(11):
            rbac.INVITE_RATE_LIMITER.reset()
            codes.append((await client.post(ACCEPT_URL, json=body)).status_code)
        assert codes[10] == 429
        assert any(":team:accept-invite:" in k for k in fake_redis.counts)

    async def test_redis_outage_keeps_the_local_limit(self, client, fake_redis):
        fake_redis.down = True
        codes = [
            (await client.post(PREVIEW_URL, json={"token": f"g-{i}"})).status_code
            for i in range(11)
        ]
        assert 429 not in codes[:10] and codes[10] == 429


# ---------------------------------------------------------------------------
# Upload size cap
# ---------------------------------------------------------------------------
class TestUploadLimitSetting:
    @pytest.mark.parametrize(
        "raw,expected",
        [(None, 10), ("25", 25), ("0.5", 1), ("500", 100), ("abc", 10), ("nan", 10)],
    )
    def test_max_upload_mb(self, monkeypatch, raw, expected):
        if raw is None:
            monkeypatch.delenv("MAX_UPLOAD_MB", raising=False)
        else:
            monkeypatch.setenv("MAX_UPLOAD_MB", raw)
        assert upload_limits.max_upload_mb() == expected

    def test_budgets(self, monkeypatch):
        monkeypatch.setenv("MAX_UPLOAD_MB", "2")
        single = upload_limits.request_body_limit(f"{API_PREFIX}/contracts")
        bulk = upload_limits.request_body_limit(f"{API_PREFIX}/contracts/bulk")
        assert single == 2 * MB + upload_limits.MULTIPART_OVERHEAD_BYTES
        assert bulk == 20 * MB + upload_limits.MULTIPART_OVERHEAD_BYTES
        assert upload_limits.request_body_limit(f"{API_PREFIX}/auth/login") is None


@pytest.fixture
def side_effects(monkeypatch):
    calls = {"reserved": 0, "released": 0, "uploaded": 0, "read": 0}

    async def _reserve(org_id):
        calls["reserved"] += 1
        return True, ""

    async def _release(org_id):
        calls["released"] += 1

    async def _s3(content, filename, org_id, mime, extension=None):
        calls["uploaded"] += 1
        return f"{org_id}/{filename}"

    async def _true(*args, **kwargs):
        return True

    async def _extract(content, mime):
        calls["read"] += 1
        return "text"

    async def _analyze(text):
        return {"summary": "ok"}

    async def _noop(**kwargs):
        return None

    monkeypatch.setattr(contracts_route, "reserve_trial_upload_atomic", _reserve)
    monkeypatch.setattr(contracts_route, "release_trial_upload", _release)
    monkeypatch.setattr(contracts_route, "upload_file_to_s3", _s3)
    monkeypatch.setattr(contracts_route, "purge_uploaded_object", _true)
    monkeypatch.setattr(contracts_route, "extract_text_from_file", _extract)
    monkeypatch.setattr(contracts_route, "analyze_contract", _analyze)
    monkeypatch.setattr(contracts_route, "log_action", _noop)
    monkeypatch.setenv("MAX_UPLOAD_MB", "1")
    return calls


@pytest.fixture
def capped_app(app):
    app.add_middleware(UploadSizeLimitMiddleware)
    return app


@pytest.fixture
async def uploader(db, org_id):
    u = make_user(org_id, role="user")
    await db.users.insert_one(dict(u))
    return u


def _nothing_happened(calls):
    return calls == {"reserved": 0, "released": 0, "uploaded": 0, "read": 0}


class TestUploadCap:
    async def test_declared_length_over_budget_is_refused_unread(
        self, capped_app, client, uploader, side_effects, db
    ):
        body = b"x" * (3 * MB)
        response = await client.post(
            f"{API_PREFIX}/contracts",
            headers={
                **auth_headers(uploader),
                "content-type": "multipart/form-data; boundary=b",
            },
            content=body,
        )
        assert response.status_code == 413
        assert "maximum upload size is 1 MB" in response.json()["detail"]
        assert _nothing_happened(side_effects)
        assert await db.contracts.count_documents({}) == 0

    async def test_streamed_body_over_budget_is_cut_off(
        self, capped_app, client, uploader, side_effects, db
    ):
        async def stream():
            yield b'--b\r\nContent-Disposition: form-data; name="title"\r\n\r\nT\r\n'
            yield (
                b'--b\r\nContent-Disposition: form-data; name="file"; '
                b'filename="big.txt"\r\nContent-Type: text/plain\r\n\r\n'
            )
            for _ in range(40):  # 40 x 256 KB = 10 MB, budget is 2 MB
                yield b"a" * (256 * 1024)
            yield b"\r\n--b--\r\n"

        response = await client.post(
            f"{API_PREFIX}/contracts",
            headers={
                **auth_headers(uploader),
                "content-type": "multipart/form-data; boundary=b",
            },
            content=stream(),
        )
        assert response.status_code == 413
        assert _nothing_happened(side_effects)
        assert await db.contracts.count_documents({}) == 0

    async def test_single_file_over_limit_within_budget(
        self, capped_app, client, uploader, side_effects, db
    ):
        response = await client.post(
            f"{API_PREFIX}/contracts",
            headers=auth_headers(uploader),
            files={"file": ("big.txt", b"a" * (MB + 10), "text/plain")},
            data={"title": "Big"},
        )
        assert response.status_code == 413
        assert _nothing_happened(side_effects)
        assert await db.contracts.count_documents({}) == 0

    async def test_bulk_with_one_oversized_file_stores_nothing(
        self, capped_app, client, uploader, side_effects, db
    ):
        response = await client.post(
            f"{API_PREFIX}/contracts/bulk",
            headers=auth_headers(uploader),
            files=[
                ("files", ("ok.txt", b"small contract", "text/plain")),
                ("files", ("big.txt", b"a" * (MB + 10), "text/plain")),
            ],
        )
        assert response.status_code == 413
        assert _nothing_happened(side_effects)
        assert await db.contracts.count_documents({}) == 0

    async def test_bulk_declared_length_over_budget(
        self, capped_app, client, uploader, side_effects
    ):
        response = await client.post(
            f"{API_PREFIX}/contracts/bulk",
            headers={
                **auth_headers(uploader),
                "content-type": "multipart/form-data; boundary=b",
            },
            content=b"x" * (12 * MB),
        )
        assert response.status_code == 413
        assert _nothing_happened(side_effects)

    async def test_files_at_the_limit_still_upload(
        self, capped_app, client, uploader, side_effects, db
    ):
        response = await client.post(
            f"{API_PREFIX}/contracts",
            headers=auth_headers(uploader),
            files={"file": ("ok.txt", b"a" * MB, "text/plain")},
            data={"title": "At limit"},
        )
        assert response.status_code == 200, response.text
        assert side_effects["reserved"] == 1 and side_effects["uploaded"] == 1
        bulk = await client.post(
            f"{API_PREFIX}/contracts/bulk",
            headers=auth_headers(uploader),
            files=[("files", (f"f{i}.txt", b"a" * MB, "text/plain")) for i in range(3)],
        )
        assert bulk.status_code == 200 and bulk.json()["successful"] == 3

    async def test_other_routes_are_not_capped(self, capped_app, client):
        response = await client.post(
            f"{API_PREFIX}/team/accept-invite/preview",
            json={"token": "t" * 100},
        )
        assert response.status_code != 413


class TestExtractedTextCap:
    """Text is stored inline on the contract; Mongo caps documents at 16 MiB."""

    @pytest.fixture
    def big_text(self, monkeypatch, side_effects):
        monkeypatch.setenv("MAX_UPLOAD_MB", "20")
        monkeypatch.setattr(upload_limits, "MAX_EXTRACTED_TEXT_BYTES", 1000)

        async def _extract(content, mime):
            side_effects["read"] += 1
            return content.decode()

        monkeypatch.setattr(contracts_route, "extract_text_from_file", _extract)
        return side_effects

    async def test_single_upload_with_too_much_text_is_413(
        self, client, uploader, big_text, db
    ):
        response = await client.post(
            f"{API_PREFIX}/contracts",
            headers=auth_headers(uploader),
            files={"file": ("big.txt", b"a" * 2000, "text/plain")},
            data={"title": "Big"},
        )
        assert response.status_code == 413
        assert "too much text" in response.json()["detail"]
        assert big_text["reserved"] == big_text["uploaded"] == 0
        assert await db.contracts.count_documents({}) == 0

    async def test_bulk_lists_the_file_and_keeps_the_rest(
        self, client, uploader, big_text, db
    ):
        response = await client.post(
            f"{API_PREFIX}/contracts/bulk",
            headers=auth_headers(uploader),
            files=[
                ("files", ("big.txt", b"a" * 2000, "text/plain")),
                ("files", ("ok.txt", b"a" * 10, "text/plain")),
            ],
        )
        body = response.json()
        assert response.status_code == 200
        assert body["successful"] == 1
        assert "too much text" in body["results"]["failed"][0]["error"]
        assert big_text["reserved"] == big_text["uploaded"] == 1
        assert await db.contracts.count_documents({}) == 1

    def test_default_limit_leaves_headroom_under_16_mib(self):
        assert upload_limits.MAX_EXTRACTED_TEXT_BYTES <= 12 * MB
