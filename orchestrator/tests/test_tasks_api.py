"""Task API: POST /api/sessions/{id}/tasks accepts a thinking level and
persists it on the Task row. The task runner's background execution is stubbed
— only the API contract and persistence are under test here."""

import pytest

from app import db as db_module
from app.models import Session, SessionState, Task, ThinkingLevel
from app.services import task_runner


@pytest.fixture
def stub_task_run(monkeypatch):
    """Stop create_task's background _run coroutine from touching containers."""

    async def noop(task_id: int) -> None:
        return None

    monkeypatch.setattr(task_runner, "_run", noop)


@pytest.fixture
def session_id(api) -> str:
    with db_module.write_session() as db:
        session = Session(name="test session", state=SessionState.running)
        db.add(session)
        db.flush()
        return session.id


class TestCreateTask:
    def test_thinking_is_accepted_and_persisted(
        self, api, auth_headers, session_id, stub_task_run
    ):
        resp = api.post(
            f"/api/sessions/{session_id}/tasks",
            headers=auth_headers,
            json={"prompt": "refactor the parser", "thinking": "high"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["prompt"] == "refactor the parser"
        assert body["thinking"] == "high"

        with db_module.read_session() as db:
            task = db.get(Task, body["id"])
        assert task.thinking == ThinkingLevel.high

        listed = api.get(
            f"/api/sessions/{session_id}/tasks", headers=auth_headers
        ).json()
        assert [t["thinking"] for t in listed] == ["high"]

    def test_thinking_defaults_to_auto(
        self, api, auth_headers, session_id, stub_task_run
    ):
        resp = api.post(
            f"/api/sessions/{session_id}/tasks",
            headers=auth_headers,
            json={"prompt": "fix the bug"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["thinking"] == "auto"

    def test_invalid_thinking_level_is_rejected(
        self, api, auth_headers, session_id, stub_task_run
    ):
        resp = api.post(
            f"/api/sessions/{session_id}/tasks",
            headers=auth_headers,
            json={"prompt": "fix the bug", "thinking": "ultra"},
        )
        assert resp.status_code == 422
        # Nothing was persisted for the rejected request.
        listed = api.get(
            f"/api/sessions/{session_id}/tasks", headers=auth_headers
        ).json()
        assert listed == []
