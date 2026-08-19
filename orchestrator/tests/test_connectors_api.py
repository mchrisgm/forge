"""Connectors API: per-user rows seeded on registration, catalog backfill for
profiles predating a catalog addition, per-user config and secrets (masked in
responses), and per-user custom MCP servers. Plus the scrapling/fetch catalog
invariants (scrapling is core + default-on; fetch is superseded/default-off)."""

from sqlmodel import select

from app import db as db_module
from app.connector_catalog import CATALOG, CORE, DEFAULT_ENABLED
from app.models import Connector
from app.routers.connectors import MASK


def connector_of(api, headers, kind: str) -> dict:
    rows = api.get("/api/connectors", headers=headers).json()
    return next(r for r in rows if r["kind"] == kind)


class TestPerUserConnectors:
    def test_registration_seeds_the_full_catalog(self, api, auth_headers):
        rows = api.get("/api/connectors", headers=auth_headers).json()
        assert {r["kind"] for r in rows} == set(CATALOG)
        github = connector_of(api, auth_headers, "github")
        assert github["enabled"] is False  # off until a PAT is configured
        # scrapling supersedes fetch as the default page reader.
        assert connector_of(api, auth_headers, "scrapling")["enabled"] is True
        assert connector_of(api, auth_headers, "fetch")["enabled"] is False

    def test_each_user_gets_their_own_seeded_rows(
        self, api, auth_headers, second_user_headers
    ):
        mine = api.get("/api/connectors", headers=auth_headers).json()
        theirs = api.get("/api/connectors", headers=second_user_headers).json()
        assert {r["kind"] for r in mine} == {r["kind"] for r in theirs} == set(CATALOG)

    def test_config_and_toggle_are_per_user(
        self, api, auth_headers, second_user_headers
    ):
        resp = api.patch(
            "/api/connectors/github",
            json={"enabled": True, "config": {"token": "ghp_only_mine"}},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["enabled"] is True
        assert body["has_token"] is True
        # The secret itself never comes back — only the mask.
        token_field = next(f for f in body["auth_fields"] if f["key"] == "token")
        assert token_field["value"] == MASK
        assert "ghp_only_mine" not in resp.text

        # The other user's github row is untouched.
        theirs = connector_of(api, second_user_headers, "github")
        assert theirs["enabled"] is False
        assert theirs["has_token"] is False

    def test_patching_an_unseeded_kind_is_404(self, api, auth_headers):
        resp = api.patch(
            "/api/connectors/no-such-kind",
            json={"enabled": True},
            headers=auth_headers,
        )
        assert resp.status_code == 404


class TestScraplingCatalogInvariants:
    def test_scrapling_is_a_core_entry_with_the_compose_endpoint(self):
        entry = next(e for e in CORE if e.id == "scrapling")
        assert entry.category == "core"
        assert entry.mcp_type == "remote"
        assert entry.url == "http://mcp-scrapling:8000/mcp"
        assert entry.auth_fields == ()  # internal network, no auth
        assert entry.docs_url == "https://github.com/D4Vinci/Scrapling"

    def test_scrapling_defaults_on_and_fetch_defaults_off(self):
        assert DEFAULT_ENABLED["scrapling"] is True
        assert DEFAULT_ENABLED["fetch"] is False
        # fetch stays in the catalog for users who want it, and says why it's
        # no longer a default.
        assert "fetch" in CATALOG
        assert "supersede" in CATALOG["fetch"].auth_note.lower()


class TestCatalogBackfill:
    def _delete_row(self, api, headers, kind: str) -> None:
        rows = api.get("/api/connectors", headers=headers).json()
        assert kind in {r["kind"] for r in rows}
        with db_module.write_session() as db:
            row = db.exec(select(Connector).where(Connector.kind == kind)).one()
            db.delete(row)

    def test_listing_backfills_entries_missing_from_older_profiles(
        self, api, auth_headers
    ):
        # Simulate a profile registered before scrapling entered the catalog.
        self._delete_row(api, auth_headers, "scrapling")

        row = connector_of(api, auth_headers, "scrapling")
        assert row["enabled"] is True  # seeded with its catalog default

    def test_backfill_is_idempotent_and_keeps_user_edits(self, api, auth_headers):
        self._delete_row(api, auth_headers, "scrapling")
        connector_of(api, auth_headers, "scrapling")
        api.patch(
            "/api/connectors/scrapling",
            json={"enabled": False},
            headers=auth_headers,
        )

        rows = api.get("/api/connectors", headers=auth_headers).json()
        scrapling_rows = [r for r in rows if r["kind"] == "scrapling"]
        assert len(scrapling_rows) == 1  # no duplicates from repeated listings
        assert scrapling_rows[0]["enabled"] is False  # user's toggle survives


class TestCustomConnectors:
    def test_custom_connectors_are_per_user(
        self, api, auth_headers, second_user_headers
    ):
        created = api.post(
            "/api/connectors/custom",
            json={
                "name": "My Server",
                "mcp_type": "remote",
                "url": "https://mcp.example.com/mcp",
            },
            headers=auth_headers,
        )
        assert created.status_code == 200, created.text
        kind = created.json()["kind"]
        assert kind == "custom-my-server"

        # The other user neither sees it nor can delete it — but CAN create
        # their own connector under the same kind (no global uniqueness).
        their_kinds = {
            r["kind"]
            for r in api.get("/api/connectors", headers=second_user_headers).json()
        }
        assert kind not in their_kinds
        assert (
            api.delete(
                f"/api/connectors/{kind}", headers=second_user_headers
            ).status_code
            == 404
        )
        same_name = api.post(
            "/api/connectors/custom",
            json={
                "name": "My Server",
                "mcp_type": "remote",
                "url": "https://other.example.com/mcp",
            },
            headers=second_user_headers,
        )
        assert same_name.status_code == 200

        # The owner can remove theirs; the other user's copy survives.
        assert api.delete(f"/api/connectors/{kind}", headers=auth_headers).json() == {
            "ok": True
        }
        assert kind not in {
            r["kind"] for r in api.get("/api/connectors", headers=auth_headers).json()
        }
        assert kind in {
            r["kind"]
            for r in api.get("/api/connectors", headers=second_user_headers).json()
        }

    def test_duplicate_custom_kind_for_the_same_user_is_409(self, api, auth_headers):
        body = {
            "name": "Twice",
            "mcp_type": "remote",
            "url": "https://mcp.example.com/mcp",
        }
        assert (
            api.post(
                "/api/connectors/custom", json=body, headers=auth_headers
            ).status_code
            == 200
        )
        assert (
            api.post(
                "/api/connectors/custom", json=body, headers=auth_headers
            ).status_code
            == 409
        )

    def test_only_custom_connectors_can_be_deleted(self, api, auth_headers):
        resp = api.delete("/api/connectors/github", headers=auth_headers)
        assert resp.status_code == 400
