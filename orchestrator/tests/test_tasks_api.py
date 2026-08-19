"""Task API: POST /api/sessions/{id}/tasks accepts a thinking level and
persists it on the Task row, and sessions/tasks are scoped to their owning
user. The task runner's background execution is stubbed — only the API
contract and persistence are under test here."""

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


def add_session_row(user_id: int | None = None, name: str = "test session") -> str:
    with db_module.write_session() as db:
        session = Session(name=name, state=SessionState.running, user_id=user_id)
        db.add(session)
        db.flush()
        return session.id


@pytest.fixture
def session_id(api) -> str:
    return add_session_row()


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

    def test_created_task_records_its_user(
        self, api, auth_headers, session_id, stub_task_run
    ):
        my_id = api.get("/api/users/me", headers=auth_headers).json()["id"]
        body = api.post(
            f"/api/sessions/{session_id}/tasks",
            headers=auth_headers,
            json={"prompt": "who am I"},
        ).json()
        with db_module.read_session() as db:
            task = db.get(Task, body["id"])
        assert task.user_id == my_id


class TestUserScoping:
    """Sessions and tasks became per-user: another profile must see 404s and
    empty listings, while NULL-owned legacy rows stay admin-only."""

    def _my_id(self, api, headers) -> int:
        return api.get("/api/users/me", headers=headers).json()["id"]

    def test_another_users_session_is_404(
        self, api, auth_headers, second_user_headers, stub_task_run
    ):
        session_id = add_session_row(user_id=self._my_id(api, auth_headers))
        assert (
            api.get(f"/api/sessions/{session_id}", headers=auth_headers).status_code
            == 200
        )
        for method, url, kwargs in [
            ("get", f"/api/sessions/{session_id}", {}),
            ("post", f"/api/sessions/{session_id}/stop", {}),
            ("delete", f"/api/sessions/{session_id}", {}),
            ("get", f"/api/sessions/{session_id}/tasks", {}),
            (
                "post",
                f"/api/sessions/{session_id}/tasks",
                {"json": {"prompt": "steal the session"}},
            ),
        ]:
            resp = getattr(api, method)(url, headers=second_user_headers, **kwargs)
            assert resp.status_code == 404, (method, url, resp.status_code)

    def test_session_listing_is_per_user_with_admin_seeing_legacy_rows(
        self, api, auth_headers, second_user_headers, stub_task_run
    ):
        mine = add_session_row(user_id=self._my_id(api, auth_headers), name="mine")
        theirs = add_session_row(
            user_id=self._my_id(api, second_user_headers), name="theirs"
        )
        legacy = add_session_row(user_id=None, name="legacy")

        admin_sees = {
            s["id"] for s in api.get("/api/sessions", headers=auth_headers).json()
        }
        other_sees = {
            s["id"]
            for s in api.get("/api/sessions", headers=second_user_headers).json()
        }
        assert admin_sees == {mine, legacy}  # first user is admin: legacy visible
        assert other_sees == {theirs}

    def test_task_listing_is_per_user(
        self, api, auth_headers, second_user_headers, stub_task_run
    ):
        my_session = add_session_row(user_id=self._my_id(api, auth_headers))
        their_session = add_session_row(
            user_id=self._my_id(api, second_user_headers)
        )
        api.post(
            f"/api/sessions/{my_session}/tasks",
            headers=auth_headers,
            json={"prompt": "my task"},
        )
        api.post(
            f"/api/sessions/{their_session}/tasks",
            headers=second_user_headers,
            json={"prompt": "their task"},
        )
        mine = api.get("/api/tasks", headers=auth_headers).json()
        theirs = api.get("/api/tasks", headers=second_user_headers).json()
        assert [t["prompt"] for t in mine] == ["my task"]
        assert [t["prompt"] for t in theirs] == ["their task"]
