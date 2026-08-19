"""Curated skill catalog + bulk skill-pack importer.

Catalog entries are pure data (repo+subdir installs through the ordinary
skills_service path), so API tests monkeypatch skills_service._clone with a
fake that materializes an in-memory repo tree — no git or network needed.
The one test that touches the real ECC clone is skipped when the clone is
not present on this machine.
"""

from pathlib import Path

import pytest

from app.services import skills_service
from app.services.skills_service import (
    PACK_INSTALL_CAP,
    PACK_SCAN_CAP,
    SkillError,
    parse_frontmatter,
)
from app.skill_catalog import CATALOG, CATEGORIES, ECC_REPO, get_entry

ECC_CLONE = Path("/workspace/affaan-m/ecc")


def skill_md(name: str, description: str = "Does things.") -> str:
    return f"---\nname: {name}\ndescription: {description}\n---\n# {name}\n"


def fake_clone(monkeypatch, skills: dict[str, str], setup=None) -> list[str]:
    """Replace skills_service._clone with a fake that writes `skills`
    (subdir -> SKILL.md content) into the clone dir. Returns the list of
    cloned URLs so tests can assert one clone per bulk call."""
    urls: list[str] = []

    def _fake(git_url: str, clone_dir: Path) -> None:
        urls.append(git_url)
        clone_dir = Path(clone_dir)
        for subdir, content in skills.items():
            d = clone_dir / subdir
            d.mkdir(parents=True, exist_ok=True)
            (d / "SKILL.md").write_text(content)
        if setup is not None:
            setup(clone_dir)

    monkeypatch.setattr(skills_service, "_clone", _fake)
    return urls


# ── catalog data invariants ─────────────────────────────────────────────────


class TestCatalogData:
    def test_names_unique_and_descriptions_nonempty(self):
        names = [entry.name for entry in CATALOG]
        assert len(names) == len(set(names))
        for entry in CATALOG:
            assert entry.name.strip(), entry
            assert entry.description.strip(), f"empty description: {entry.name}"

    def test_categories_are_known(self):
        for entry in CATALOG:
            assert entry.category in CATEGORIES, entry.name

    def test_entries_point_at_ecc_subdirs_over_https(self):
        for entry in CATALOG:
            assert entry.repo == ECC_REPO
            assert entry.repo.startswith("https://")
            assert entry.subdir.startswith("skills/"), entry.name

    def test_get_entry(self):
        assert get_entry(CATALOG[0].name) is CATALOG[0]
        assert get_entry("no-such-skill") is None

    @pytest.mark.skipif(not ECC_CLONE.is_dir(), reason="ECC clone not available")
    def test_every_curated_subdir_exists_in_the_real_clone(self):
        for entry in CATALOG:
            path = ECC_CLONE / entry.subdir / "SKILL.md"
            assert path.is_file(), f"{entry.name}: {path} missing upstream"
            meta = parse_frontmatter(path.read_text(encoding="utf-8", errors="replace"))
            # The installed flag matches on Skill.name, which install() takes
            # from frontmatter — so the catalog name must equal it.
            installed_name = str(meta.get("name") or Path(entry.subdir).name)
            assert installed_name == entry.name, entry.subdir


# ── catalog API ─────────────────────────────────────────────────────────────


class TestCatalogApi:
    def test_listing_shape_and_installed_flag(self, api, auth_headers, monkeypatch):
        resp = api.get("/api/skills/catalog", headers=auth_headers)
        assert resp.status_code == 200, resp.text
        entries = resp.json()
        assert len(entries) == len(CATALOG)
        first = entries[0]
        assert set(first) == {"name", "description", "category", "repo", "subdir", "installed"}
        assert all(not e["installed"] for e in entries)

        target = CATALOG[0]
        fake_clone(monkeypatch, {target.subdir: skill_md(target.name)})
        resp = api.post(
            "/api/skills/catalog/install", json={"name": target.name}, headers=auth_headers
        )
        assert resp.status_code == 200, resp.text

        entries = api.get("/api/skills/catalog", headers=auth_headers).json()
        flags = {e["name"]: e["installed"] for e in entries}
        assert flags[target.name] is True
        assert sum(flags.values()) == 1

    def test_install_returns_the_skill_row_enabled(self, api, auth_headers, monkeypatch):
        target = CATALOG[0]
        urls = fake_clone(monkeypatch, {target.subdir: skill_md(target.name, "Fake desc.")})
        resp = api.post(
            "/api/skills/catalog/install", json={"name": target.name}, headers=auth_headers
        )
        assert resp.status_code == 200, resp.text
        row = resp.json()
        assert row["name"] == target.name
        assert row["description"] == "Fake desc."
        assert row["source_url"] == target.repo
        assert row["enabled"] is True  # single installs keep the default
        assert urls == [target.repo]
        listed = api.get("/api/skills", headers=auth_headers).json()
        assert target.name in [s["name"] for s in listed]

    def test_install_unknown_name_404(self, api, auth_headers):
        resp = api.post(
            "/api/skills/catalog/install", json={"name": "no-such-skill"}, headers=auth_headers
        )
        assert resp.status_code == 404

    def test_install_twice_409(self, api, auth_headers, monkeypatch):
        target = CATALOG[0]
        fake_clone(monkeypatch, {target.subdir: skill_md(target.name)})
        body = {"name": target.name}
        assert (
            api.post("/api/skills/catalog/install", json=body, headers=auth_headers).status_code
            == 200
        )
        resp = api.post("/api/skills/catalog/install", json=body, headers=auth_headers)
        assert resp.status_code == 409
        assert "already installed" in resp.json()["detail"]

    def test_slug_collision_is_rejected_not_clobbered(
        self, api, auth_headers, monkeypatch
    ):
        # Two differently-named skills that slugify to the same dir must not
        # share on-disk storage — the second install is a 409, not a silent
        # rmtree of the first.
        from app.services import skills_service

        fake_clone(monkeypatch, {"": skill_md("Web Search")})
        first = skills_service.install("https://github.com/x/a")
        assert first.name == "Web Search"
        fake_clone(monkeypatch, {"": skill_md("web-search")})
        with pytest.raises(skills_service.SkillError) as exc:
            skills_service.install("https://github.com/x/b")
        assert exc.value.status_code == 409


# ── pack scan ───────────────────────────────────────────────────────────────


class TestPackScan:
    def test_scan_lists_depth1_and_depth2_skills(self, api, auth_headers, monkeypatch):
        fake_clone(
            monkeypatch,
            {
                "solo": skill_md("solo", "Top-level skill."),
                "skills/alpha": skill_md("alpha", "First."),
                "skills/beta": skill_md("beta", "Second."),
            },
        )
        resp = api.post(
            "/api/skills/pack/scan",
            json={"git_url": "https://github.com/acme/pack"},
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        entries = {e["subdir"]: e for e in resp.json()}
        assert set(entries) == {"solo", "skills/alpha", "skills/beta"}
        assert entries["skills/alpha"]["name"] == "alpha"
        assert entries["skills/alpha"]["description"] == "First."
        assert "note" not in entries["skills/alpha"]

    def test_scan_tolerates_malformed_frontmatter_with_a_note(
        self, api, auth_headers, monkeypatch
    ):
        fake_clone(
            monkeypatch,
            {
                "skills/good": skill_md("good"),
                "skills/broken": "---\n: [unbalanced\n---\nbody\n",
                "skills/bare": "# No frontmatter at all\n",
            },
        )
        resp = api.post(
            "/api/skills/pack/scan",
            json={"git_url": "https://github.com/acme/pack"},
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        entries = {e["subdir"]: e for e in resp.json()}
        assert "note" not in entries["skills/good"]
        for subdir in ("skills/broken", "skills/bare"):
            assert entries[subdir]["name"] == subdir.split("/")[1]  # dir-name fallback
            assert entries[subdir]["description"] == ""
            assert "frontmatter" in entries[subdir]["note"]

    def test_scan_caps_results(self, monkeypatch):
        many = {f"skills/s{i:03d}": skill_md(f"s{i:03d}") for i in range(PACK_SCAN_CAP + 5)}
        fake_clone(monkeypatch, many)
        entries = skills_service.scan_pack("https://github.com/acme/pack")
        assert len(entries) == PACK_SCAN_CAP

    def test_scan_rejects_non_https_urls(self, api, auth_headers):
        for bad in ("git@github.com:acme/pack.git", "http://github.com/acme/pack", "ftp://x/y"):
            resp = api.post(
                "/api/skills/pack/scan", json={"git_url": bad}, headers=auth_headers
            )
            assert resp.status_code == 400, bad
            assert "https" in resp.json()["detail"]


# ── pack install ────────────────────────────────────────────────────────────


class TestPackInstall:
    PACK = {
        "skills/alpha": skill_md("alpha", "First."),
        "skills/beta": skill_md("beta", "Second."),
        "skills/gamma": skill_md("gamma", "Third."),
    }

    def test_bulk_install_is_one_clone_and_defaults_disabled(
        self, api, auth_headers, monkeypatch, tmp_path
    ):
        urls = fake_clone(monkeypatch, self.PACK)
        resp = api.post(
            "/api/skills/pack/install",
            json={
                "git_url": "https://github.com/acme/pack",
                "subdirs": ["skills/alpha", "skills/beta"],
            },
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        result = resp.json()
        assert result["installed"] == ["alpha", "beta"]
        assert result["skipped"] == []
        assert "disabled" in result["note"]
        assert urls == ["https://github.com/acme/pack"]  # one clone for the batch

        # The app seeds bundled skills at startup, so filter to what we added.
        rows = {
            s["name"]: s
            for s in api.get("/api/skills", headers=auth_headers).json()
            if s["name"] in ("alpha", "beta")
        }
        assert set(rows) == {"alpha", "beta"}
        for row in rows.values():
            assert row["enabled"] is False
            assert (Path(row["path"]) / "SKILL.md").is_file()
            assert str(tmp_path) in row["path"]  # landed in the tmp skills volume

    def test_bad_subdirs_are_skipped_with_reasons(self, api, auth_headers, monkeypatch):
        def add_symlink_escape(clone_dir: Path) -> None:
            evil = clone_dir / "skills" / "evil"
            evil.mkdir(parents=True)
            (evil / "SKILL.md").write_text(skill_md("evil"))
            (evil / "steal.txt").symlink_to("/etc/hostname")

        fake_clone(monkeypatch, self.PACK, setup=add_symlink_escape)
        resp = api.post(
            "/api/skills/pack/install",
            json={
                "git_url": "https://github.com/acme/pack",
                "subdirs": ["skills/alpha", "skills/missing", "skills/evil", "skills/alpha"],
            },
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        result = resp.json()
        assert result["installed"] == ["alpha"]
        reasons = {s["subdir"]: s["reason"] for s in result["skipped"]}
        assert "no SKILL.md" in reasons["skills/missing"]
        assert "symlink escaping" in reasons["skills/evil"]
        # The duplicate selection hits the same guard as a duplicate install.
        assert reasons["skills/alpha"] == "skill 'alpha' is already installed"

    def test_install_cap_enforced(self, api, auth_headers):
        resp = api.post(
            "/api/skills/pack/install",
            json={
                "git_url": "https://github.com/acme/pack",
                "subdirs": [f"skills/s{i}" for i in range(PACK_INSTALL_CAP + 1)],
            },
            headers=auth_headers,
        )
        assert resp.status_code == 400
        assert str(PACK_INSTALL_CAP) in resp.json()["detail"]

    def test_empty_selection_400(self, api, auth_headers, monkeypatch):
        fake_clone(monkeypatch, self.PACK)
        resp = api.post(
            "/api/skills/pack/install",
            json={"git_url": "https://github.com/acme/pack", "subdirs": []},
            headers=auth_headers,
        )
        assert resp.status_code == 400

    def test_install_rejects_non_https_urls(self, api, auth_headers):
        resp = api.post(
            "/api/skills/pack/install",
            json={"git_url": "git://github.com/acme/pack", "subdirs": ["skills/alpha"]},
            headers=auth_headers,
        )
        assert resp.status_code == 400
        assert "https" in resp.json()["detail"]

    def test_service_validates_before_cloning(self, monkeypatch):
        urls = fake_clone(monkeypatch, self.PACK)
        with pytest.raises(SkillError):
            skills_service.install_from_pack("https://github.com/acme/pack", [])
        with pytest.raises(SkillError):
            skills_service.install_from_pack(
                "https://github.com/acme/pack",
                [f"skills/s{i}" for i in range(PACK_INSTALL_CAP + 1)],
            )
        assert urls == []  # neither call reached the clone
