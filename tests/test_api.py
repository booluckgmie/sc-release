import json

import pytest
from fastapi.testclient import TestClient

import api.index as mod

DOCS = [
    {"source_document_id": "a", "title": "Equity Guidelines", "document_type": "main", "category": "guidelines",
     "topic": "equity", "document_family_id": "equity-guidelines", "year": 2026, "sort_date": "2026-09-30",
     "dates": {"revised": "2026-09-30"}, "parent_document_id": None,
     "collection": {"download_status": "pending", "still_listed": True}},
    {"source_document_id": "b", "title": "Equity Guidelines", "document_type": "main", "category": "guidelines",
     "topic": "equity", "document_family_id": "equity-guidelines", "year": 2017, "sort_date": "2017-01-19",
     "dates": {"revised": "2017-01-19", "applicable_until": "2019-03-12"}, "parent_document_id": None,
     "collection": {"download_status": "ok", "still_listed": True, "file_sha256": "x", "date_check": "exact"}},
]


@pytest.fixture
def client(tmp_path, monkeypatch):
    p = tmp_path / "catalogue.json"
    p.write_text(json.dumps({"generated_at": "t", "documents": DOCS}))
    monkeypatch.setattr(mod, "CATALOGUE", p)
    mod._cat.cache_clear()
    return TestClient(mod.app)


def test_filters_and_pagination(client):
    r = client.get("/v1/documents", params={"limit": 1}).json()
    assert r["meta"]["total_matches"] == 2 and r["meta"]["next_cursor"]
    r2 = client.get("/v1/documents", params={"limit": 1, "cursor": r["meta"]["next_cursor"]}).json()
    assert r2["data"][0]["source_document_id"] == "b" and r2["meta"]["next_cursor"] is None
    assert client.get("/v1/documents", params={"year": 2017}).json()["meta"]["total_matches"] == 1
    r = client.get("/v1/documents", params={"date_field": "applicable_until", "date_from": "2019-01-01"})
    assert r.json()["data"][0]["source_document_id"] == "b"


def test_lookup_and_errors(client):
    assert client.get("/v1/documents/a").json()["data"]["title"] == "Equity Guidelines"
    assert client.get("/v1/documents/zzz").status_code == 404
    assert client.get("/v1/documents", params={"date_field": "nope"}).status_code == 400
    assert client.get("/v1/stats").json()["downloaded_and_hashed"] == 1
    assert client.get("/v1/families").json()["data"][0]["versions"] == 2


def test_home_page(client):
    r = client.get("/")
    assert r.status_code == 200 and "Equity Guidelines" in r.text
    assert "2 document(s)" in r.text
    assert "1 document(s)" in client.get("/", params={"year": "2017"}).text
    assert "No documents match" in client.get("/", params={"q": "zzz"}).text
