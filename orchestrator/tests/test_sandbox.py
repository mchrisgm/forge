"""Sandbox lane: the smolvm client (``app.services.sandbox``) and its authed
REST surface (``/api/sandbox``).

The client is exercised against the shared ``httpx_mock`` MockTransport so the
exact smolvm request shape is pinned — a future smolvm API change (image per
language, ``timeoutSecs``, the ``network: false`` guest lockdown, the
env-delivered code) breaks these on purpose. The endpoints are exercised with
``sandbox.run_code`` / ``sandbox.available`` monkeypatched, so router auth and
validation are tested without any VM.
"""

import json

import httpx
import pytest

from app.services import sandbox
from app.services.sandbox import RUNNER_NAME, SandboxError

# ── helpers ─────────────────────────────────────────────────────────────────


def _requests_to(httpx_mock, suffix: str) -> list[httpx.Request]:
    return [r for r in httpx_mock.requests if r.url.path.endswith(suffix)]


def _two_stage_handler(run_response: httpx.Response):
    """A handler that 200s the create call and returns ``run_response`` for run."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/machines":
            return httpx.Response(200, json={"name": RUNNER_NAME, "state": "created"})
        if request.url.path.endswith("/run"):
            return run_response
        return httpx.Response(404, text="unexpected path")

    return handler


def _exec_response(**overrides) -> httpx.Response:
    body = {
        "exitCode": 0,
        "stdout": "",
        "stderr": "",
        "stdoutB64": "",
        "stderrB64": "",
    }
    body.update(overrides)
    return httpx.Response(200, json=body)


# ── run_code: happy path + request shape ────────────────────────────────────


async def test_run_code_happy_path(httpx_mock):
    httpx_mock.set_handler(
        _two_stage_handler(_exec_response(exitCode=0, stdout="hello\n"))
    )

    result = await sandbox.run_code("python", "print('hello')")

    assert result["exit_code"] == 0
    assert result["stdout"] == "hello\n"
    assert result["stderr"] == ""
    assert result["timed_out"] is False
    assert result["duration_ms"] >= 0


async def test_run_code_sends_expected_smolvm_run_shape(httpx_mock):
    httpx_mock.set_handler(_two_stage_handler(_exec_response()))

    await sandbox.run_code("python", "print('hi')", stdin="42\n", timeout_s=15)

    (run_req,) = _requests_to(httpx_mock, "/run")
    assert run_req.method == "POST"
    assert run_req.url.path == f"/api/v1/machines/{RUNNER_NAME}/run"
    body = json.loads(run_req.content)

    # Pinned base image for the language.
    assert body["image"] == "python:3.12-alpine"
    # Hard timeout is passed through (camelCase per RunRequest).
    assert body["timeoutSecs"] == 15
    # Code is delivered as data via env vars, never interpolated into argv.
    assert body["command"][0] == "/bin/sh"
    assert "print('hi')" not in json.dumps(body["command"])
    env = {e["name"]: e["value"] for e in body["env"]}
    assert env["FORGE_CODE"] == "print('hi')"
    assert env["FORGE_STDIN"] == "42\n"


async def test_run_code_creates_a_network_off_runner(httpx_mock):
    """The guest must have NO egress — assert the create body disables network."""
    httpx_mock.set_handler(_two_stage_handler(_exec_response()))

    await sandbox.run_code("bash", "echo hi")

    (create_req,) = _requests_to(httpx_mock, "/api/v1/machines")
    assert create_req.method == "POST"
    create_body = json.loads(create_req.content)
    assert create_body["network"] is False
    assert create_body["name"] == RUNNER_NAME


async def test_run_code_image_per_language(httpx_mock):
    for language, expected_image in [
        ("python", "python:3.12-alpine"),
        ("javascript", "node:22-alpine"),
        ("node", "node:22-alpine"),
        ("bash", "alpine"),
    ]:
        httpx_mock.requests.clear()
        httpx_mock.set_handler(_two_stage_handler(_exec_response()))
        await sandbox.run_code(language, "x")
        (run_req,) = _requests_to(httpx_mock, "/run")
        assert json.loads(run_req.content)["image"] == expected_image


async def test_run_code_clamps_timeout_to_ceiling(httpx_mock):
    httpx_mock.set_handler(_two_stage_handler(_exec_response()))
    await sandbox.run_code("python", "x", timeout_s=9999)
    (run_req,) = _requests_to(httpx_mock, "/run")
    assert json.loads(run_req.content)["timeoutSecs"] == 60


# ── run_code: nonzero exit / timeout / truncation ───────────────────────────


async def test_run_code_nonzero_exit(httpx_mock):
    httpx_mock.set_handler(
        _two_stage_handler(
            _exec_response(exitCode=1, stdout="", stderr="Traceback ...")
        )
    )
    result = await sandbox.run_code("python", "raise SystemExit(1)")
    assert result["exit_code"] == 1
    assert result["stderr"] == "Traceback ..."
    assert result["timed_out"] is False


async def test_run_code_timeout_sets_timed_out(httpx_mock):
    # smolvm SIGKILLs at the deadline and reports exit code 124.
    httpx_mock.set_handler(_two_stage_handler(_exec_response(exitCode=124)))
    result = await sandbox.run_code("python", "while True: pass", timeout_s=1)
    assert result["exit_code"] == 124
    assert result["timed_out"] is True


async def test_run_code_truncates_output(httpx_mock):
    huge = "x" * (60 * 1024)
    httpx_mock.set_handler(_two_stage_handler(_exec_response(stdout=huge)))
    result = await sandbox.run_code("python", "print('x' * 999999)")
    assert len(result["stdout"].encode("utf-8")) <= sandbox.MAX_OUTPUT_BYTES + 100
    assert result["stdout"].endswith("40 KB limit]")
    assert len(result["stdout"]) < len(huge)


# ── run_code: error mapping ─────────────────────────────────────────────────


async def test_run_code_unsupported_language_raises_valueerror(httpx_mock):
    with pytest.raises(ValueError) as excinfo:
        await sandbox.run_code("ruby", "puts 1")
    assert "unsupported language" in str(excinfo.value)
    # Rejected before any HTTP call is made.
    assert httpx_mock.requests == []


async def test_run_code_lane_down_is_503(httpx_mock):
    def refused(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    httpx_mock.set_handler(refused)
    with pytest.raises(SandboxError) as excinfo:
        await sandbox.run_code("python", "x")
    assert excinfo.value.status == 503
    assert "make sandbox" in excinfo.value.message


async def test_run_code_smolvm_failure_is_502(httpx_mock):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/machines":
            return httpx.Response(200, json={"name": RUNNER_NAME})
        return httpx.Response(500, text="vm boot failed")

    httpx_mock.set_handler(handler)
    with pytest.raises(SandboxError) as excinfo:
        await sandbox.run_code("python", "x")
    assert excinfo.value.status == 502


async def test_run_code_provision_conflict_is_ok(httpx_mock):
    """A 409 on create means the runner already exists — not an error."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/machines":
            return httpx.Response(409, json={"error": "already exists"})
        return _exec_response(stdout="ok\n")

    httpx_mock.set_handler(handler)
    result = await sandbox.run_code("python", "print('ok')")
    assert result["stdout"] == "ok\n"


# ── available() ─────────────────────────────────────────────────────────────


async def test_available_healthy(httpx_mock):
    httpx_mock.set_handler(
        lambda request: httpx.Response(200, json={"status": "ok"})
    )
    info = await sandbox.available()
    assert info == {"enabled": True, "healthy": True, "detail": "ok"}
    (probe,) = httpx_mock.requests
    assert probe.url.path == "/health"


async def test_available_down_never_raises(httpx_mock):
    def refused(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    httpx_mock.set_handler(refused)
    info = await sandbox.available()
    assert info["enabled"] is True
    assert info["healthy"] is False
    assert "unreachable" in info["detail"]


async def test_available_unhealthy_status(httpx_mock):
    httpx_mock.set_handler(lambda request: httpx.Response(503, text="starting"))
    info = await sandbox.available()
    assert info["healthy"] is False
    assert "503" in info["detail"]


# ── REST endpoints ──────────────────────────────────────────────────────────


def test_run_endpoint_requires_auth(api):
    resp = api.post("/api/sandbox/run", json={"language": "python", "code": "x"})
    assert resp.status_code == 401


def test_status_endpoint_requires_auth(api):
    assert api.get("/api/sandbox/status").status_code == 401


def test_run_endpoint_happy(api, auth_headers, monkeypatch):
    async def fake_run_code(language, code, stdin="", timeout_s=30):
        return {
            "stdout": "hi\n",
            "stderr": "",
            "exit_code": 0,
            "timed_out": False,
            "duration_ms": 12,
        }

    monkeypatch.setattr(sandbox, "run_code", fake_run_code)
    resp = api.post(
        "/api/sandbox/run",
        headers=auth_headers,
        json={"language": "python", "code": "print('hi')"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["stdout"] == "hi\n"
    assert resp.json()["exit_code"] == 0


def test_run_endpoint_unsupported_language_is_400(api, auth_headers, monkeypatch):
    async def fake_run_code(language, code, stdin="", timeout_s=30):
        raise ValueError("unsupported language 'ruby'")

    monkeypatch.setattr(sandbox, "run_code", fake_run_code)
    resp = api.post(
        "/api/sandbox/run",
        headers=auth_headers,
        json={"language": "ruby", "code": "puts 1"},
    )
    assert resp.status_code == 400
    assert "unsupported language" in resp.json()["detail"]


def test_run_endpoint_oversize_code_is_400(api, auth_headers):
    resp = api.post(
        "/api/sandbox/run",
        headers=auth_headers,
        json={"language": "python", "code": "x" * (100 * 1024 + 1)},
    )
    assert resp.status_code == 400
    assert "100 KB" in resp.json()["detail"]


def test_run_endpoint_lane_down_is_503(api, auth_headers, monkeypatch):
    async def fake_run_code(language, code, stdin="", timeout_s=30):
        raise SandboxError("sandbox lane not running", status=503)

    monkeypatch.setattr(sandbox, "run_code", fake_run_code)
    resp = api.post(
        "/api/sandbox/run",
        headers=auth_headers,
        json={"language": "python", "code": "x"},
    )
    assert resp.status_code == 503


def test_run_endpoint_smolvm_failure_is_502(api, auth_headers, monkeypatch):
    async def fake_run_code(language, code, stdin="", timeout_s=30):
        raise SandboxError("smolvm run failed", status=502)

    monkeypatch.setattr(sandbox, "run_code", fake_run_code)
    resp = api.post(
        "/api/sandbox/run",
        headers=auth_headers,
        json={"language": "python", "code": "x"},
    )
    assert resp.status_code == 502


def test_status_endpoint_reports_availability(api, auth_headers, monkeypatch):
    async def fake_available():
        return {"enabled": True, "healthy": True, "detail": "ok"}

    monkeypatch.setattr(sandbox, "available", fake_available)
    resp = api.get("/api/sandbox/status", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["healthy"] is True
    assert body["url"] == "http://smolvm:9000"
