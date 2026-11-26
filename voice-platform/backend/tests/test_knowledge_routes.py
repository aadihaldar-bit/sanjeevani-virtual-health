import io
import pytest
from fastapi import FastAPI, Depends
from fastapi.testclient import TestClient
from unittest.mock import patch, AsyncMock

from app import knowledge_routes
from app.rag_engine import LocalHybridRAGEngine
from app.database import get_db


class _NoDB:
    async def execute(self, *a, **k):
        class R:
            def scalars(self_inner):
                class S:
                    def first(self_i2):
                        return None
                return S()
        return R()


async def _fake_db():
    yield _NoDB()


@pytest.fixture
def client(tmp_path):
    engine = LocalHybridRAGEngine(kb_dir=str(tmp_path / "kb"))
    app = FastAPI()
    app.include_router(knowledge_routes.router)
    app.dependency_overrides[get_db] = _fake_db
    with patch.object(knowledge_routes, "rag_engine", engine), \
         patch.object(knowledge_routes.dashboard_manager, "broadcast", new_callable=AsyncMock):
        yield TestClient(app)


def upload(client, name, data, **extra):
    return client.post("/api/knowledge/upload", files={"file": (name, io.BytesIO(data), "text/plain")}, data=extra)


def test_upload_list_search_delete_roundtrip(client):
    r = upload(client, "Warranty Terms.md", b"# Warranty\n\n## Period\n\nThe warranty period is twenty four months from purchase date.")
    assert r.status_code == 200 and r.json()["chunks"] >= 1
    doc_id = r.json()["document_id"]

    listed = client.get("/api/knowledge/documents").json()
    assert listed["total"] == 1 and listed["documents"][0]["name"] == "Warranty Terms.md"
    assert listed["documents"][0]["chunks"] == r.json()["chunks"]          # real count, not a placeholder

    s = client.post("/api/knowledge/search", json={"query": "how long is the warranty period"}).json()
    assert s["results"] and s["results"][0]["doc_id"] == doc_id and "timings_ms" in s

    assert client.delete(f"/api/knowledge/documents/{doc_id}").status_code == 200
    assert client.get("/api/knowledge/documents").json()["total"] == 0
    assert client.post("/api/knowledge/search", json={"query": "how long is the warranty period"}).json()["results"] == []
    assert client.delete(f"/api/knowledge/documents/{doc_id}").status_code == 404


def test_upload_rejects_bad_files_with_real_errors(client):
    assert upload(client, "x.exe", b"MZ" * 50).status_code == 400
    assert upload(client, "empty.txt", b"").status_code == 400
    r = upload(client, "blank.txt", b"   ")
    assert r.status_code == 400 and "No readable text" in r.json()["detail"]
    assert client.get("/api/knowledge/documents").json()["total"] == 0     # nothing fake stored


def test_filename_is_sanitised(client):
    r = upload(client, "../../etc/pass wd?.md", b"# Notes\n\nThis document talks about the clinic opening hours in detail.")
    assert r.status_code == 200
    assert r.json()["name"] == "pass wd_.md"


def test_status_and_reindex_without_vector_store(client):
    assert client.get("/api/knowledge/status").json()["vector"]["enabled"] is False
    assert client.post("/api/knowledge/reindex").json()["enabled"] is False
