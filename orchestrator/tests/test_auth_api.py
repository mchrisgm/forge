"""Multi-user auth API tests: register/login/status flows, first-user-admin,
username validation, the registration toggle, bearer/query-param gating, and
the per-profile password change. The legacy single-password login is gone."""

from tests.conftest import (
    SECOND_PASSWORD,
    SECOND_USERNAME,
    TEST_PASSWORD,
    TEST_USERNAME,
    register_user,
)


def me(api, headers) -> dict:
    resp = api.get("/api/users/me", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


class TestStatus:
    def test_fresh_install_requires_setup_and_allows_registration(self, api):
        resp = api.get("/api/auth/status")
        assert resp.status_code == 200
        assert resp.json() == {
            "setup_required": True,
            "allow_registration": True,
            "user_count": 0,
        }

    def test_after_first_registration_setup_is_done(self, api, auth_headers):
        body = api.get("/api/auth/status").json()
        assert body["setup_required"] is False
        assert body["user_count"] == 1
        assert body["allow_registration"] is True


class TestRegister:
    def test_register_returns_jwt_and_profile(self, api):
        resp = api.post(
            "/api/auth/register",
            json={
                "username": TEST_USERNAME,
                "password": TEST_PASSWORD,
                "display_name": "The Tester",
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        token = body["token"]
        assert isinstance(token, str) and token.count(".") == 2  # JWT shape
        assert body["user"]["username"] == TEST_USERNAME
        assert body["user"]["display_name"] == "The Tester"
        assert body["user"]["is_admin"] is True  # first profile is the admin
        assert "password_hash" not in body["user"]

    def test_first_user_is_admin_second_is_not(self, api, auth_headers, second_user_headers):
        assert me(api, auth_headers)["is_admin"] is True
        assert me(api, second_user_headers)["is_admin"] is False

    def test_display_name_defaults_to_username(self, api):
        headers = register_user(api)
        assert me(api, headers)["display_name"] == TEST_USERNAME

    def test_username_is_normalized_to_lowercase(self, api):
        resp = api.post(
            "/api/auth/register",
            json={"username": "  MixedCase ", "password": TEST_PASSWORD},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["user"]["username"] == "mixedcase"
        login = api.post(
            "/api/auth/login", json={"username": "mixedcase", "password": TEST_PASSWORD}
        )
        assert login.status_code == 200

    def test_duplicate_username_is_409(self, api, auth_headers):
        resp = api.post(
            "/api/auth/register",
            json={"username": TEST_USERNAME, "password": "different-pass"},
        )
        assert resp.status_code == 409

    def test_duplicate_is_case_insensitive(self, api, auth_headers):
        resp = api.post(
            "/api/auth/register",
            json={"username": TEST_USERNAME.upper(), "password": "different-pass"},
        )
        assert resp.status_code == 409

    def test_invalid_usernames_are_400(self, api):
        for bad in ["ab", "-starts-with-dash", "has space", "dots.not.ok", "x" * 33]:
            resp = api.post(
                "/api/auth/register", json={"username": bad, "password": TEST_PASSWORD}
            )
            assert resp.status_code == 400, bad
            assert "username" in resp.json()["detail"]

    def test_short_password_is_400(self, api):
        resp = api.post(
            "/api/auth/register", json={"username": TEST_USERNAME, "password": "short"}
        )
        assert resp.status_code == 400
        assert "password" in resp.json()["detail"]

    def test_avatar_color_is_assigned_on_creation(self, api):
        headers = register_user(api)
        assert me(api, headers)["avatar_color"].startswith("#")


class TestLogin:
    def test_right_credentials_return_token(self, api, auth_headers):
        resp = api.post(
            "/api/auth/login",
            json={"username": TEST_USERNAME, "password": TEST_PASSWORD},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["token"].count(".") == 2
        assert body["user"]["username"] == TEST_USERNAME

    def test_wrong_password_is_401(self, api, auth_headers):
        resp = api.post(
            "/api/auth/login",
            json={"username": TEST_USERNAME, "password": "not-the-password"},
        )
        assert resp.status_code == 401
        assert "token" not in resp.json()

    def test_unknown_username_is_401(self, api, auth_headers):
        resp = api.post(
            "/api/auth/login", json={"username": "nobody", "password": TEST_PASSWORD}
        )
        assert resp.status_code == 401

    def test_legacy_password_only_login_is_400(self, api, auth_headers):
        """The v1 single-password shape must be rejected with a helpful hint,
        not accepted and not a 500."""
        resp = api.post("/api/auth/login", json={"password": TEST_PASSWORD})
        assert resp.status_code == 400
        assert "multi-user" in resp.json()["detail"]

    def test_missing_password_field_is_422(self, api):
        assert api.post("/api/auth/login", json={}).status_code == 422


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

    def test_token_of_deleted_profile_is_401(self, api, auth_token):
        from sqlmodel import delete

        from app.db import write_session
        from app.models import User

        with write_session() as db:
            db.exec(delete(User))
        resp = api.get(
            "/api/sessions", headers={"Authorization": f"Bearer {auth_token}"}
        )
        assert resp.status_code == 401

    def test_auth_check_endpoint(self, api, auth_headers):
        assert api.get("/api/auth/check").status_code == 401
        resp = api.get("/api/auth/check", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        assert resp.json()["user"]["username"] == TEST_USERNAME


class TestRegistrationToggle:
    def test_toggle_is_admin_only(self, api, auth_headers, second_user_headers):
        resp = api.post(
            "/api/users/registration",
            json={"allow_registration": False},
            headers=second_user_headers,
        )
        assert resp.status_code == 403
        # And of course anonymous callers can't reach it either.
        assert (
            api.post(
                "/api/users/registration", json={"allow_registration": False}
            ).status_code
            == 401
        )

    def test_disabled_registration_is_enforced(self, api, auth_headers):
        resp = api.post(
            "/api/users/registration",
            json={"allow_registration": False},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert api.get("/api/auth/status").json()["allow_registration"] is False

        blocked = api.post(
            "/api/auth/register",
            json={"username": "latecomer", "password": TEST_PASSWORD},
        )
        assert blocked.status_code == 403

        # The admin can re-open it, after which registration works again.
        resp = api.post(
            "/api/users/registration",
            json={"allow_registration": True},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert (
            api.post(
                "/api/auth/register",
                json={"username": "latecomer", "password": TEST_PASSWORD},
            ).status_code
            == 200
        )


class TestPasswordChange:
    NEW_PASSWORD = "a-brand-new-password"

    def _change(self, api, headers, current, new):
        return api.post(
            "/api/users/me/password",
            json={"current_password": current, "new_password": new},
            headers=headers,
        )

    def test_change_requires_auth(self, api):
        resp = api.post(
            "/api/users/me/password",
            json={"current_password": TEST_PASSWORD, "new_password": self.NEW_PASSWORD},
        )
        assert resp.status_code == 401

    def test_wrong_current_password_is_401(self, api, auth_headers):
        resp = self._change(api, auth_headers, "wrong", self.NEW_PASSWORD)
        assert resp.status_code == 401

    def test_short_new_password_is_400(self, api, auth_headers):
        resp = self._change(api, auth_headers, TEST_PASSWORD, "short")
        assert resp.status_code == 400
        # And the old password still logs in.
        login = api.post(
            "/api/auth/login",
            json={"username": TEST_USERNAME, "password": TEST_PASSWORD},
        )
        assert login.status_code == 200

    def test_full_change_flow(self, api, auth_headers):
        resp = self._change(api, auth_headers, TEST_PASSWORD, self.NEW_PASSWORD)
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}

        # Old password no longer logs in; the new one does.
        assert (
            api.post(
                "/api/auth/login",
                json={"username": TEST_USERNAME, "password": TEST_PASSWORD},
            ).status_code
            == 401
        )
        login = api.post(
            "/api/auth/login",
            json={"username": TEST_USERNAME, "password": self.NEW_PASSWORD},
        )
        assert login.status_code == 200

        # Fresh token from the new password gates routes as usual.
        new_token = login.json()["token"]
        resp = api.get(
            "/api/sessions", headers={"Authorization": f"Bearer {new_token}"}
        )
        assert resp.status_code == 200

    def test_change_only_touches_the_calling_profile(
        self, api, auth_headers, second_user_headers
    ):
        resp = self._change(api, second_user_headers, SECOND_PASSWORD, self.NEW_PASSWORD)
        assert resp.status_code == 200
        # The first user's password is untouched.
        assert (
            api.post(
                "/api/auth/login",
                json={"username": TEST_USERNAME, "password": TEST_PASSWORD},
            ).status_code
            == 200
        )
        assert (
            api.post(
                "/api/auth/login",
                json={"username": SECOND_USERNAME, "password": self.NEW_PASSWORD},
            ).status_code
            == 200
        )


class TestProfile:
    def test_patch_me_updates_profile_fields(self, api, auth_headers):
        resp = api.patch(
            "/api/users/me",
            json={
                "display_name": "Renamed",
                "personal_instructions": "always answer in haiku",
                "memory_enabled": False,
                "avatar_color": "#123456",
            },
            headers=auth_headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["display_name"] == "Renamed"
        assert body["personal_instructions"] == "always answer in haiku"
        assert body["memory_enabled"] is False
        assert body["avatar_color"] == "#123456"

    def test_personal_instructions_are_capped_at_4000_chars(self, api, auth_headers):
        resp = api.patch(
            "/api/users/me",
            json={"personal_instructions": "x" * 5000},
            headers=auth_headers,
        )
        assert len(resp.json()["personal_instructions"]) == 4000

    def test_user_listing_shows_public_info_only(
        self, api, auth_headers, second_user_headers
    ):
        resp = api.get("/api/users", headers=second_user_headers)
        assert resp.status_code == 200
        rows = resp.json()
        assert [r["username"] for r in rows] == [TEST_USERNAME, SECOND_USERNAME]
        assert [r["is_admin"] for r in rows] == [True, False]
        for row in rows:
            assert "password_hash" not in row
            assert "personal_instructions" not in row
