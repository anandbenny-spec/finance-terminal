"""`GET /api/admin/labelling/items` (Spec 02B §5.8; job 2B-12, #175): the admin gate, a blind reply with the passages' text, no
reply to anyone else, a bare 503 when the store fails, and the Data API call the deployed store makes."""
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from ft.api import labelling
from ft.api.admin_auth import AdminUser, AuthSettings, require_admin
from ft.api.app import app

ITEMS = [{"id": "earnings_tone:11-3", "document_id": 11, "chunk_no": 3, "section": "opening", "group": "open_close",
          "stratum": "fell", "slug": "acme", "season": "Q2 FY27", "peer_group": "banks"},
         {"id": "earnings_tone:12-5", "document_id": 12, "chunk_no": 5, "section": "qa", "group": "answers",
          "stratum": "rose", "slug": "beta", "season": "Q1 FY27", "peer_group": "it"}]
TEXT = {(11, 3): "Passage one text.", (12, 5): "Passage two text."}


class Store:
    def __init__(self, fail=False):
        self.fail, self.asked = fail, []

    def passages(self, refs):
        self.asked.append(refs)
        if self.fail:
            raise labelling.StoreError("boom with no text")
        return {r: TEXT[r] for r in refs if r in TEXT}


@pytest.fixture
def client(monkeypatch, tmp_path):
    f = tmp_path / "items.json"
    f.write_text(json.dumps({"set": "earnings_tone", "items": ITEMS}))
    monkeypatch.setattr(labelling, "ITEMS_PATH", f)
    store = Store()
    app.dependency_overrides.update({require_admin: lambda: AdminUser("a", "a@example.com"), labelling.passage_store: lambda: store})
    yield TestClient(app), store, f
    app.dependency_overrides.clear()


def test_the_reply_is_blind_and_holds_the_passages_text(client):
    c, store, _ = client
    r = c.get("/api/admin/labelling/items")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    assert r.json() == {"set": "earnings_tone", "count": 2, "items": [
        {"id": "earnings_tone:11-3", "section": "opening", "text": "Passage one text."},
        {"id": "earnings_tone:12-5", "section": "qa", "text": "Passage two text."}]}
    body = r.text
    for hidden in ("acme", "beta", "fell", "rose", "Q2 FY27", "banks"):
        assert hidden not in body                                    # nothing that could colour a label
    assert store.asked == [[(11, 3), (12, 5)]]


def test_an_unknown_set_is_a_404(client):
    assert client[0].get("/api/admin/labelling/items?set=policy_hawk_dove").status_code == 404


def test_a_set_with_no_items_is_an_empty_list_and_asks_the_store_nothing(client):
    c, store, f = client
    f.write_text(json.dumps({"items": []}))
    assert c.get("/api/admin/labelling/items").json() == {"set": "earnings_tone", "count": 0, "items": []}
    assert store.asked == []


def test_a_failing_store_is_a_bare_503(client):
    c, store, _ = client
    store.fail = True
    r = c.get("/api/admin/labelling/items")
    assert r.status_code == 503 and "boom" not in r.text and "Passage" not in r.text


def test_the_committed_items_file_loads():
    assert labelling.set_items() is not None


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer"}, {"Authorization": "Basic abc"}, {"Authorization": "Bearer a.b.c"}])
def test_without_the_admin_token_nothing_is_returned(headers):
    app.dependency_overrides.clear()
    r = TestClient(app).get("/api/admin/labelling/items", headers=headers)
    assert r.status_code == 401 and "Passage" not in r.text


def test_the_deployed_store_calls_the_data_api_function_with_the_secret_key():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=[{"document_id": 11, "chunk_no": 3, "text": "T1"}])

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        store = labelling.RpcPassageStore(AuthSettings("https://x.supabase.co", "secret-key"), http)
        assert store.passages([(11, 3)]) == {(11, 3): "T1"}
    (req,) = seen
    assert str(req.url) == "https://x.supabase.co/rest/v1/rpc/admin_labelling_passages"
    assert req.headers["authorization"] == "Bearer secret-key" and req.headers["content-profile"] == "api"
    assert json.loads(req.content) == {"p_items": [{"document_id": 11, "chunk_no": 3}]}


@pytest.mark.parametrize("response", [httpx.Response(404), httpx.Response(200, text="not json"), httpx.Response(200, json=[{"x": 1}])])
def test_a_missing_function_or_a_bad_answer_is_a_store_error(response):
    with httpx.Client(transport=httpx.MockTransport(lambda r: response)) as http:
        store = labelling.RpcPassageStore(AuthSettings("https://x.supabase.co", "k"), http)
        with pytest.raises(labelling.StoreError):
            store.passages([(1, 1)])
