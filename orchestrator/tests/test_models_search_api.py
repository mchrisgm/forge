"""The Models-page Hub search: GET /api/models/search and POST
/api/models/search/add. search_hub / resolve_text_candidate / snapshot_size_gb
are patched on the ROUTER module (they are imported into its namespace), and
downloader.start_download is a recorder — the Hub is never dialed."""

from types import SimpleNamespace

import pytest
from sqlmodel import select

from app import db as db_module
from app.models import ModelEntry
from app.routers import models_api as models_api_module
from app.routers.models_api import LANE_NOTE
from app.services import downloader, registry

from .conftest import add_model

SEARCHED_REPO = "stabilityai/sdxl-turbo"
TEXT_REPO = "Qwen/Qwen2.5-Coder-7B-Instruct"


@pytest.fixture
def download_spy(monkeypatch) -> list[str]:
    calls: list[str] = []

    async def fake_start_download(entry, token=None) -> None:
        calls.append(entry.hf_repo)

    monkeypatch.setattr(downloader, "start_download", fake_start_download)
    return calls


@pytest.fixture
def snapshot_stub(monkeypatch) -> None:
    monkeypatch.setattr(
        models_api_module, "snapshot_size_gb", lambda repo, token=None: 6.94
    )
    # Image adds are gated on diffusers format; stub it so no test ever
    # touches the real Hub.
    monkeypatch.setattr(
        models_api_module, "is_diffusers_repo", lambda repo, token=None: True
    )


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
        models_api_module, "resolve_text_candidate", lambda repo, token=None: dict(state)
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

        def fake_search_hub(query, kind, limit, token=None):
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
        def failing_search(query, kind, limit, token=None):
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

    def test_non_diffusers_image_repo_is_409(
        self, api, auth_headers, download_spy, monkeypatch
    ):
        # text-to-image covers raw-checkpoint/LoRA repos (e.g.
        # ByteDance/SDXL-Lightning) the imagegen server cannot load — refuse
        # them at add time instead of after a multi-GB download.
        monkeypatch.setattr(
            models_api_module, "is_diffusers_repo", lambda repo, token=None: False
        )
        monkeypatch.setattr(
            models_api_module, "snapshot_size_gb", lambda repo, token=None: 46.1
        )
        resp = api.post(
            "/api/models/search/add",
            json={"hf_repo": SEARCHED_REPO, "kind": "image"},
            headers=auth_headers,
        )
        assert resp.status_code == 409
        assert "model_index.json" in resp.json()["detail"]
        assert download_spy == []
        with db_module.read_session() as db:
            assert db.exec(select(ModelEntry)).all() == []

    def test_gguf_rewrite_readds_are_409(
        self, api, auth_headers, download_spy, resolved
    ):
        # The llamacpp lane stores the RESOLVED quantizer repo, so a second
        # add of the SEARCHED repo must be caught by the artifact dedupe —
        # a duplicate row would share the slug (hard 409 at load) and its
        # deletion would rmtree the surviving row's weights.
        resolved.update(
            lane="llamacpp-full-gpu",
            gguf_repo="bartowski/Qwen2.5-Coder-7B-Instruct-GGUF",
            gguf_file="qwen2.5-coder-7b-instruct-q4_k_m.gguf",
            gguf_size_gb=4.7,
        )
        first = api.post(
            "/api/models/search/add",
            json={"hf_repo": TEXT_REPO, "kind": "text"},
            headers=auth_headers,
        )
        assert first.status_code == 200, first.text
        # Simulate the downloader's file_path rewrite — dedupe must survive it.
        with db_module.write_session() as db:
            row = db.exec(select(ModelEntry)).one()
            row.file_path = (
                "gguf/bartowski__Qwen2.5-Coder-7B-Instruct-GGUF/"
                "qwen2.5-coder-7b-instruct-q4_k_m.gguf"
            )
            db.add(row)
        second = api.post(
            "/api/models/search/add",
            json={"hf_repo": TEXT_REPO, "kind": "text"},
            headers=auth_headers,
        )
        assert second.status_code == 409
        assert "already in the catalog" in second.json()["detail"]
        # Only the first add downloaded (entries record the RESOLVED repo).
        assert download_spy == ["bartowski/Qwen2.5-Coder-7B-Instruct-GGUF"]
        with db_module.read_session() as db:
            assert len(db.exec(select(ModelEntry)).all()) == 1

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
        def failing_resolve(repo, token=None):
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


# ── registry-level helpers behind the endpoints ─────────────────────────────


class FakeHubApi:
    """Stands in for huggingface_hub.HfApi inside registry.search_hub /
    is_diffusers_repo — construction args ignored, state injected per test."""

    listing: list = []
    files: list = []
    error: Exception | None = None

    def __init__(self, token=None):
        pass

    def list_models(self, **kwargs):
        return list(type(self).listing)

    def list_repo_files(self, repo):
        if type(self).error is not None:
            raise type(self).error
        return list(type(self).files)


@pytest.fixture
def hub(monkeypatch) -> type[FakeHubApi]:
    import huggingface_hub

    FakeHubApi.listing, FakeHubApi.files, FakeHubApi.error = [], [], None
    monkeypatch.setattr(huggingface_hub, "HfApi", FakeHubApi)
    return FakeHubApi


class TestInCatalogFlag:
    def test_display_name_match_survives_the_gguf_rewrite(self, api, hub):
        # After add(), the row's hf_repo is the quantizer repo but its
        # display_name is the searched repo's name — the flag must see it.
        hub.listing = [
            SimpleNamespace(
                id=TEXT_REPO, tags=[], created_at=None,
                downloads=10, likes=1, gated=False,
            )
        ]
        add_model(
            hf_repo="bartowski/Qwen2.5-Coder-7B-Instruct-GGUF",
            display_name="Qwen2.5-Coder-7B-Instruct",
        )
        rows = registry.search_hub("qwen", "text", 5)
        assert rows[0]["in_catalog"] is True

    def test_unrelated_models_stay_addable(self, api, hub):
        hub.listing = [
            SimpleNamespace(
                id="org/other-model", tags=[], created_at=None,
                downloads=10, likes=1, gated=False,
            )
        ]
        add_model()
        assert registry.search_hub("other", "text", 5)[0]["in_catalog"] is False


class TestIsDiffusersRepo:
    def test_requires_model_index_json(self, api, hub):
        hub.files = ["model_index.json", "unet/config.json"]
        assert registry.is_diffusers_repo("org/pipe") is True
        hub.files = ["sd_xl_turbo_1.0.safetensors", "config.json"]
        assert registry.is_diffusers_repo("org/raw") is False

    def test_listing_failure_is_false(self, api, hub):
        hub.error = RuntimeError("hub down")
        assert registry.is_diffusers_repo("org/pipe") is False
