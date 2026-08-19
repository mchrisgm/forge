"""Auth API tests (PLAN §7): single-password login, bearer gating, query-param
tokens for SSE clients, and the password-change flow."""

from tests.conftest import TEST_PASSWORD


class TestLogin:
    def test_wrong_password_is_401(self, api):
        resp = api.post("/api/auth/login", json={"password": "not-the-password"})
        assert resp.status_code == 401
        assert "token" not in resp.json()

    def test_right_password_returns_token(self, api):
        resp = api.post("/api/auth/login", json={"password": TEST_PASSWORD})
        assert resp.status_code == 200
        token = resp.json()["token"]
        assert isinstance(token, str) and len(token) > 20
        # JWTs are three dot-separated segments.
        assert token.count(".") == 2

    def test_missing_password_field_is_422(self, api):
        assert api.post("/api/auth/login", json={}).status_code == 422

    def test_empty_password_is_401(self, api):
        assert api.post("/api/auth/login", json={"password": ""}).status_code == 401


class TestGating:
    def test_health_needs_no_auth(self, api):
        resp = api.get("/api/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}

    def test_protected_route_without_token_is_401(self, api):
        assert api.get("/api/sessions").status_code == 401

    def test_protected_route_with_garbage_token_is_401(self, api):
        resp = api.get(
            "/api/sessions", headers={"Authorization": "Bearer not.a.token"}
        )
        assert resp.status_code == 401

    def test_protected_route_with_bearer_header(self, api, auth_headers):
        resp = api.get("/api/sessions", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json() == []

    def test_token_in_query_param_accepted(self, api, auth_token):
        # EventSource can't set headers, so ?token= must work (PLAN §6.1 SSE).
        resp = api.get("/api/sessions", params={"token": auth_token})
        assert resp.status_code == 200

    def test_garbage_query_token_is_401(self, api):
        assert api.get("/api/sessions", params={"token": "junk"}).status_code == 401

    def test_auth_check_endpoint(self, api, auth_headers):
        assert api.get("/api/auth/check").status_code == 401
        assert api.get("/api/auth/check", headers=auth_headers).status_code == 200


class TestPasswordChange:
    NEW_PASSWORD = "a-brand-new-password"

    def test_change_requires_auth(self, api):
        resp = api.post(
            "/api/settings/password",
            json={"current_password": TEST_PASSWORD, "new_password": self.NEW_PASSWORD},
        )
        assert resp.status_code == 401

    def test_wrong_current_password_is_401(self, api, auth_headers):
        resp = api.post(
            "/api/settings/password",
            json={"current_password": "wrong", "new_password": self.NEW_PASSWORD},
            headers=auth_headers,
        )
        assert resp.status_code == 401

    def test_short_new_password_is_400(self, api, auth_headers):
        resp = api.post(
            "/api/settings/password",
            json={"current_password": TEST_PASSWORD, "new_password": "short"},
            headers=auth_headers,
        )
        assert resp.status_code == 400
        # And the old password still works.
        assert (
            api.post("/api/auth/login", json={"password": TEST_PASSWORD}).status_code
            == 200
        )

    def test_full_change_flow(self, api, auth_headers):
        resp = api.post(
            "/api/settings/password",
            json={"current_password": TEST_PASSWORD, "new_password": self.NEW_PASSWORD},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}

        # Old password no longer logs in; the new one does.
        assert (
            api.post("/api/auth/login", json={"password": TEST_PASSWORD}).status_code
            == 401
        )
        login = api.post("/api/auth/login", json={"password": self.NEW_PASSWORD})
        assert login.status_code == 200

        # Fresh token from the new password gates routes as usual.
        new_token = login.json()["token"]
        resp = api.get(
            "/api/sessions", headers={"Authorization": f"Bearer {new_token}"}
        )
        assert resp.status_code == 200
