"""bundled_skills.seed_bundled_skills: seeding the repo's vendored skills into
the skills volume + Skill table, idempotence across reboots, preservation of
user edits, restoration of wiped volume files, and the symlink-escape guard
inherited from skills_service."""

from pathlib import Path

import pytest
from sqlmodel import select

from app import config
from app import db as db_module
from app.models import Skill
from app.services import bundled_skills
from app.services.bundled_skills import seed_bundled_skills

pytestmark = pytest.mark.usefixtures("db_ready")


def skill_rows() -> list[Skill]:
    with db_module.read_session() as db:
        return list(db.exec(select(Skill)).all())


def make_bundle(root: Path, dirname: str = "demo", name: str = "demo-skill") -> Path:
    skill_dir = root / dirname
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: A vendored demo skill\n---\n# Demo\n"
    )
    (skill_dir / "LICENSE").write_text("BSD 3-Clause License\n")
    refs = skill_dir / "references"
    refs.mkdir()
    (refs / "guide.md").write_text("guide\n")
    return skill_dir


@pytest.fixture
def fake_root(tmp_path, monkeypatch) -> Path:
    root = tmp_path / "bundled"
    root.mkdir()
    monkeypatch.setattr(bundled_skills, "bundled_root", lambda: root)
    return root


class TestSeeding:
    def test_seeds_copy_files_and_register_a_row(self, fake_root):
        make_bundle(fake_root)
        assert seed_bundled_skills() == 1

        (row,) = skill_rows()
        assert row.name == "demo-skill"
        assert row.description == "A vendored demo skill"
        assert row.source_url == "bundled://demo"
        assert row.enabled is True
        dest = Path(config.get_settings().skills_dir) / "demo-skill"
        assert row.path == str(dest)
        assert (dest / "SKILL.md").is_file()
        assert (dest / "LICENSE").is_file()
        assert (dest / "references" / "guide.md").is_file()

    def test_missing_bundled_dir_is_a_noop(self, monkeypatch):
        monkeypatch.setattr(bundled_skills, "bundled_root", lambda: None)
        assert seed_bundled_skills() == 0
        assert skill_rows() == []

    def test_dir_without_skill_md_is_skipped(self, fake_root):
        (fake_root / "junk").mkdir()
        (fake_root / "junk" / "notes.txt").write_text("no skill here")
        make_bundle(fake_root)
        assert seed_bundled_skills() == 1
        assert [row.name for row in skill_rows()] == ["demo-skill"]

    def test_loose_files_at_the_root_are_ignored(self, fake_root):
        (fake_root / "README.md").write_text("about the bundles")
        assert seed_bundled_skills() == 0


class TestIdempotence:
    def test_second_run_adds_nothing_and_keeps_one_row(self, fake_root):
        make_bundle(fake_root)
        assert seed_bundled_skills() == 1
        assert seed_bundled_skills() == 0
        assert len(skill_rows()) == 1

    def test_user_edits_to_the_row_survive_reseeding(self, fake_root):
        make_bundle(fake_root)
        seed_bundled_skills()
        with db_module.write_session() as db:
            row = db.exec(select(Skill)).one()
            row.enabled = False
            db.add(row)

        assert seed_bundled_skills() == 0
        (row,) = skill_rows()
        assert row.enabled is False

    def test_wiped_volume_files_are_restored(self, fake_root):
        import shutil

        make_bundle(fake_root)
        seed_bundled_skills()
        dest = Path(skill_rows()[0].path)
        shutil.rmtree(dest)

        assert seed_bundled_skills() == 0  # no new row…
        assert (dest / "SKILL.md").is_file()  # …but the files are back
        assert len(skill_rows()) == 1


class TestSymlinkGuard:
    def test_escaping_symlink_blocks_that_bundle_only(self, fake_root, tmp_path):
        secret = tmp_path / "secret.txt"
        secret.write_text("jwt-signing-key")
        evil = make_bundle(fake_root, dirname="evil", name="evil-skill")
        (evil / "steal.txt").symlink_to(secret)
        make_bundle(fake_root, dirname="good", name="good-skill")

        assert seed_bundled_skills() == 1
        assert [row.name for row in skill_rows()] == ["good-skill"]
        assert not (Path(config.get_settings().skills_dir) / "evil-skill").exists()


class TestRealRepoBundle:
    def test_the_vendored_scrapling_skill_seeds_from_the_repo(self):
        # No monkeypatching: resolution falls back to the repo root, where the
        # trimmed Scrapling skill is vendored.
        assert seed_bundled_skills() == 1
        (row,) = skill_rows()
        assert row.name == "scrapling-official"
        assert "scrap" in row.description.lower()
        assert row.source_url == "bundled://scrapling"
        dest = Path(row.path)
        assert dest == Path(config.get_settings().skills_dir) / "scrapling-official"
        assert (dest / "SKILL.md").is_file()
        assert (dest / "LICENSE").is_file()
        assert (dest / "references" / "mcp-server.md").is_file()
        # Provenance travels with the vendored copy.
        skill_md = (dest / "SKILL.md").read_text()
        assert "D4Vinci/Scrapling v0.4.14" in skill_md
        assert "BSD-3-Clause" in skill_md
        assert "BSD 3-Clause License" in (dest / "LICENSE").read_text()
        # Idempotent against the real bundle too.
        assert seed_bundled_skills() == 0
