"""Analysis truncation is labelled: when the contract text exceeds the
analysis (8,000 chars) or chat (6,000 chars) cap, the API says the result is
partial and how much was analysed. No chunking."""
from types import SimpleNamespace

import pytest

from routes import contracts as contracts_route
from services import ai_service
from tests.unit.conftest import API_PREFIX, auth_headers, make_contract

LONG = "x" * 20_000
SHORT = "This agreement is between A and B."


def test_coverage_helper():
    full = ai_service.analysis_coverage(SHORT)
    assert full == {"analysisPartial": False, "analyzedChars": len(SHORT), "totalChars": len(SHORT),
                    "approxPagesAnalyzed": 1}
    part = ai_service.analysis_coverage(LONG)
    assert part["analysisPartial"] is True and part["analyzedChars"] == 8000 and part["totalChars"] == 20_000
    assert part["approxPagesAnalyzed"] == 3
    chat = ai_service.analysis_coverage(LONG, ai_service.CHAT_MAX_CHARS)
    assert (chat["analyzedChars"], chat["approxPagesAnalyzed"]) == (6000, 2)
    exact = ai_service.analysis_coverage("y" * 8000)
    assert exact["analysisPartial"] is False
    assert ai_service.analysis_coverage(None)["totalChars"] == 0


async def test_model_sees_exactly_the_caps(monkeypatch):
    sent = []

    class FakeChat:
        def __init__(self, **kwargs):
            pass

        def with_model(self, *a):
            return self

        async def send_message(self, message):
            sent.append(message.text)
            return '{"summary": "ok"}'

    monkeypatch.setattr(ai_service, "EMERGENT_LLM_KEY", "test-key")
    monkeypatch.setattr(ai_service, "LlmChat", FakeChat)
    monkeypatch.setattr(ai_service, "UserMessage", lambda text: SimpleNamespace(text=text))
    await ai_service.analyze_contract("a" * 8000 + "b" * 100)
    await ai_service.get_chat_response("c" * 6000 + "d" * 100, "Q?", "s")
    assert "a" * 8000 in sent[0] and "b" not in sent[0]
    assert "c" * 6000 in sent[1] and "d" not in sent[1]


@pytest.fixture
def stub_upload(monkeypatch):
    state = {"text": SHORT, "analysis": {"riskLevel": "low", "dates": {}, "summary": "s"}}

    async def fake_extract(content, mime):
        return state["text"]

    async def fake_s3(content, filename, org_id, mime):
        return f"{org_id}/{filename}"

    async def fake_analyze(text):
        return state["analysis"]

    monkeypatch.setattr(contracts_route, "extract_text_from_file", fake_extract)
    monkeypatch.setattr(contracts_route, "upload_file_to_s3", fake_s3)
    monkeypatch.setattr(contracts_route, "analyze_contract", fake_analyze)
    return state


async def _upload(client, user):
    return await client.post(f"{API_PREFIX}/contracts", data={"title": "MSA"},
                             files={"file": ("msa.txt", b"x", "text/plain")}, headers=auth_headers(user))


async def test_long_contract_upload_is_marked_partial(client, users, stub_upload):
    stub_upload["text"] = LONG
    resp = await _upload(client, users["user"])
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["analysisPartial"] is True
    assert (body["analyzedChars"], body["totalChars"], body["approxPagesAnalyzed"]) == (8000, 20_000, 3)
    detail = (await client.get(f"{API_PREFIX}/contracts/{body['id']}", headers=auth_headers(users["user"]))).json()
    assert detail["analysisPartial"] is True and detail["approxPagesAnalyzed"] == 3
    listed = (await client.get(f"{API_PREFIX}/contracts", headers=auth_headers(users["user"]))).json()
    items = listed if isinstance(listed, list) else listed.get("items", listed.get("contracts", []))
    assert items and items[0]["analysisPartial"] is True


async def test_short_contract_is_not_partial(client, users, stub_upload):
    body = (await _upload(client, users["user"])).json()
    assert body["analysisPartial"] is False and body["analyzedChars"] == len(SHORT)


async def test_failed_analysis_has_no_coverage(client, users, stub_upload):
    stub_upload["text"] = LONG
    stub_upload["analysis"] = None
    body = (await _upload(client, users["user"])).json()
    assert body["aiAnalysisStatus"] == "failed" and body["analysisPartial"] is None


async def test_bulk_upload_records_coverage(client, db, users, stub_upload):
    stub_upload["text"] = LONG
    resp = await client.post(f"{API_PREFIX}/contracts/bulk", files=[("files", ("a.txt", b"x", "text/plain"))],
                             headers=auth_headers(users["user"]))
    assert resp.status_code == 200
    doc = await db.contracts.find_one({})
    assert doc["analysisPartial"] is True and doc["analyzedChars"] == 8000


async def test_legacy_contract_has_null_coverage(client, db, org_id, owner):
    await db.contracts.insert_one(make_contract(org_id, owner["id"], contract_id="c-old"))
    body = (await client.get(f"{API_PREFIX}/contracts/c-old", headers=auth_headers(owner))).json()
    assert body["analysisPartial"] is None


@pytest.mark.parametrize("text,partial", [(LONG, True), (SHORT, False)])
async def test_chat_reports_partial_context(client, db, org_id, owner, monkeypatch, text, partial):
    async def fake_chat(t, q, s):
        return "answer"

    monkeypatch.setattr(contracts_route, "get_chat_response", fake_chat)
    await db.contracts.insert_one(make_contract(org_id, owner["id"], contract_id="c-chat", originalText=text))
    resp = await client.post(f"{API_PREFIX}/contracts/c-chat/chat", json={"question": "Q?"}, headers=auth_headers(owner))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["contextPartial"] is partial
    assert body["contextChars"] == min(len(text), 6000)
