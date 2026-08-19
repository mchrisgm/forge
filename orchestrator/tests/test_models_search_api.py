"""The Models-page Hub search: GET /api/models/search and POST
/api/models/search/add. search_hub / resolve_text_candidate / snapshot_size_gb
are patched on the ROUTER module (they are imported into its namespace), and
downloader.start_download is a recorder — the Hub is never dialed."""

import pytest
from sqlmodel import select

from app import db as db_module
from app.models import ModelEntry
from app.routers import models_api as models_api_module
from app.routers.models_api import LANE_NOTE
from app.services import downloader

from .conftest import add_model

SEARCHED_REPO = "stabilityai/sdxl-turbo"
TEXT_REPO = "Qwen/Qwen2.5-Coder-7B-Instruct"


@pytest.fixture
def download_spy(monkeypatch) -> list[str]:
    calls: list[str] = []

    async def fake_start_download(entry) -> None:
        calls.append(entry.hf_repo)

    monkeypatch.setattr(downloader, "start_download", fake_start_download)
    return calls


@pytest.fixture
def snapshot_stub(monkeypatch) -> None:
    monkeypatch.setattr(models_api_module, "snapshot_size_gb", lambda repo: 6.94)


@pytest.fixture
def resolved(monkeypatch) -> dict:
    """resolve_text_candidate stub; tests mutate the dict per scenario."""
    state = {
        "lane": "vllm",
        "params_b": 7.6,
        "is_moe": False,
        "gguf_repo": None,
        "gguf_file": None,
        "gguf_size_gb": 0.0,
    }
    monkeypatch.setattr(
        models_api_module, "resolve_text_candidate", lambda repo: dict(state)
    )
    return state


def entry_for(hf_repo: str) -> ModelEntry:
    with db_module.read_session() as db:
        entry = db.exec(
            select(ModelEntry).where(ModelEntry.hf_repo == hf_repo)
        ).first()
    assert entry is not None, f"no ModelEntry for {hf_repo}"
    return entry


# ── GET /api/models/search ──────────────────────────────────────────────────


class TestSearchModels:
    def test_requires_auth(self, api):
        assert api.get("/api/models/search", params={"q": "qwen"}).status_code == 401

    def test_bad_kind_is_400(self, api, auth_headers):
        resp = api.get(
            "/api/models/search",
            params={"q": "qwen", "kind": "video"},
            headers=auth_headers,
        )
        assert resp.status_code == 400
        assert "kind" in resp.json()["detail"]

    def test_blank_query_is_400(self, api, auth_headers):
        resp = api.get(
            "/api/models/search", params={"q": "   "}, headers=auth_headers
        )
        assert resp.status_code == 400

    def test_returns_the_hub_results(self, api, auth_headers, monkeypatch):
        results = [
            {"hf_repo": SEARCHED_REPO, "downloads": 12345, "in_catalog": False}
        ]
        calls: list[tuple] = []

        def fake_search_hub(query, kind, limit):
            calls.append((query, kind, limit))
            return results

        monkeypatch.setattr(models_api_module, "search_hub", fake_search_hub)
        resp = api.get(
            "/api/models/search",
            params={"q": "  sdxl turbo  ", "kind": "image", "limit": 5},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json() == results
        assert calls == [("sdxl turbo", "image", 5)]

    def test_hub_failure_is_502(self, api, auth_headers, monkeypatch):
        def failing_search(query, kind, limit):
            raise RuntimeError("HF is down")

        monkeypatch.setattr(models_api_module, "search_hub", failing_search)
        resp = api.get(
            "/api/models/search", params={"q": "qwen"}, headers=auth_headers
        )
        assert resp.status_code == 502
        assert "HF is down" in resp.json()["detail"]


# ── POST /api/models/search/add ─────────────────────────────────────────────


class TestAddFromSearch:
    @pytest.mark.parametrize(
        "hf_repo", ["noslash", "a/b/c", "owner/", "/name", "owner name/model"]
    )
    def test_malformed_repo_is_400(self, api, auth_headers, download_spy, hf_repo):
        resp = api.post(
            "/api/models/search/add",
            json={"hf_repo": hf_repo},
            headers=auth_headers,
        )
        assert resp.status_code == 400
        assert download_spy == []

    def test_repo_already_in_catalog_is_409(self, api, auth_headers, download_spy):
        add_model(hf_repo=SEARCHED_REPO)
        resp = api.post(
            "/api/models/search/add",
            json={"hf_repo": SEARCHED_REPO, "kind": "image"},
            headers=auth_headers,
        )
        assert resp.status_code == 409
        assert SEARCHED_REPO in resp.json()["detail"]
        assert download_spy == []

    def test_bad_kind_is_400(self, api, auth_headers, download_spy):
        resp = api.post(
            "/api/models/search/add",
            json={"hf_repo": SEARCHED_REPO, "kind": "video"},
            headers=auth_headers,
        )
        assert resp.status_code == 400
        assert download_spy == []

    def test_image_kind_becomes_an_imagegen_snapshot_entry(
        self, api, auth_headers, download_spy, snapshot_stub
    ):
        resp = api.post(
            "/api/models/search/add",
            json={"hf_repo": SEARCHED_REPO, "kind": "image"},
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["engine"] == "imagegen"
        assert body["quant"] == "fp16-diffusers"
        assert body["hf_repo"] == SEARCHED_REPO
        assert body["display_name"] == "sdxl-turbo"
        assert body["file_path"] == ""
        assert body["size_gb"] == 6.94
        assert body["tool_call_format"] == "none"
        assert body["status"] == "approved"
        # The entry persisted and the download started for it.
        assert entry_for(SEARCHED_REPO).id == body["id"]
        assert download_spy == [SEARCHED_REPO]

    @pytest.mark.parametrize(
        ("lane", "engine", "quant"),
        [("vllm", "vllm", "awq"), ("airllm", "airllm", "fp16-airllm")],
    )
    def test_snapshot_lanes_keep_the_searched_repo(
        self, api, auth_headers, download_spy, resolved, lane, engine, quant
    ):
        resolved["lane"] = lane
        resp = api.post(
            "/api/models/search/add",
            json={"hf_repo": TEXT_REPO, "kind": "text"},
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["engine"] == engine
        assert body["quant"] == quant
        assert body["hf_repo"] == TEXT_REPO
        assert body["file_path"] == ""
        assert body["params_b"] == 7.6
        assert body["tool_call_format"] == "hermes"
        assert body["note"] == LANE_NOTE[lane]
        assert download_spy == [TEXT_REPO]

    @pytest.mark.parametrize("lane", ["llamacpp-full-gpu", "llamacpp-offload"])
    def test_gguf_lanes_point_at_the_discovered_artifact(
        self, api, auth_headers, download_spy, resolved, lane
    ):
        resolved.update(
            lane=lane,
            gguf_repo="bartowski/Qwen2.5-Coder-7B-Instruct-GGUF",
            gguf_file="qwen2.5-coder-7b-instruct-q4_k_m.gguf",
            gguf_size_gb=4.7,
        )
        resp = api.post(
            "/api/models/search/add",
            json={"hf_repo": TEXT_REPO, "kind": "text"},
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["engine"] == "llamacpp"
        assert body["quant"] == "gguf-q4_k_m"
        assert body["hf_repo"] == "bartowski/Qwen2.5-Coder-7B-Instruct-GGUF"
        assert body["file_path"] == "qwen2.5-coder-7b-instruct-q4_k_m.gguf"
        assert body["size_gb"] == 4.7
        # The display name stays the model the user searched for.
        assert body["display_name"] == TEXT_REPO.split("/")[-1]
        assert body["note"] == LANE_NOTE[lane]
        assert download_spy == ["bartowski/Qwen2.5-Coder-7B-Instruct-GGUF"]

    def test_in_repo_gguf_falls_back_to_the_searched_repo(
        self, api, auth_headers, download_spy, resolved
    ):
        resolved.update(
            lane="llamacpp-offload", gguf_repo=None, gguf_file="model-q4_k_m.gguf"
        )
        resp = api.post(
            "/api/models/search/add",
            json={"hf_repo": TEXT_REPO, "kind": "text"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["hf_repo"] == TEXT_REPO

    def test_no_lane_is_409(self, api, auth_headers, download_spy, resolved):
        resolved["lane"] = None
        resp = api.post(
            "/api/models/search/add",
            json={"hf_repo": TEXT_REPO, "kind": "text"},
            headers=auth_headers,
        )
        assert resp.status_code == 409
        assert "does not fit" in resp.json()["detail"]
        assert download_spy == []
        with db_module.read_session() as db:
            assert db.exec(select(ModelEntry)).all() == []

    def test_gguf_lane_without_a_file_is_409(
        self, api, auth_headers, download_spy, resolved
    ):
        resolved.update(lane="llamacpp-offload", gguf_repo=None, gguf_file=None)
        resp = api.post(
            "/api/models/search/add",
            json={"hf_repo": TEXT_REPO, "kind": "text"},
            headers=auth_headers,
        )
        assert resp.status_code == 409
        assert "GGUF" in resp.json()["detail"]
        assert download_spy == []

    def test_resolution_failure_is_502(self, api, auth_headers, download_spy, monkeypatch):
        def failing_resolve(repo):
            raise RuntimeError("model_info exploded")

        monkeypatch.setattr(models_api_module, "resolve_text_candidate", failing_resolve)
        resp = api.post(
            "/api/models/search/add",
            json={"hf_repo": TEXT_REPO, "kind": "text"},
            headers=auth_headers,
        )
        assert resp.status_code == 502
        assert "model_info exploded" in resp.json()["detail"]
        assert download_spy == []

    def test_auto_download_false_skips_the_downloader(
        self, api, auth_headers, download_spy, snapshot_stub
    ):
        resp = api.post(
            "/api/models/search/add",
            json={"hf_repo": SEARCHED_REPO, "kind": "image", "auto_download": False},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert download_spy == []
        # The entry still landed in the catalog.
        assert entry_for(SEARCHED_REPO).engine.value == "imagegen"
