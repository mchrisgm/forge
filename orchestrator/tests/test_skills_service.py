"""skills_service unit tests: frontmatter, slugs, and the symlink-escape guard
(review finding: a malicious repo must not be able to pull orchestrator files
into the session-readable /skills volume)."""

import pytest

from app.services.skills_service import (
    SkillError,
    _reject_escaping_symlinks,
    parse_frontmatter,
    slugify,
)


class TestFrontmatter:
    def test_parses_name_and_description(self):
        text = "---\nname: My Skill\ndescription: Does things\n---\n# Body\n"
        meta = parse_frontmatter(text)
        assert meta == {"name": "My Skill", "description": "Does things"}

    def test_no_frontmatter_returns_empty(self):
        assert parse_frontmatter("# Just a heading\n") == {}

    def test_malformed_yaml_returns_empty(self):
        assert parse_frontmatter("---\n: [unbalanced\n---\n") == {}


class TestSlugify:
    def test_basic(self):
        assert slugify("My Cool Skill!") == "my-cool-skill"

    def test_empty_falls_back(self):
        assert slugify("***") == "skill"


class TestSymlinkGuard:
    def _make_clone(self, tmp_path):
        clone = tmp_path / "clone"
        skill = clone / "skill"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text("---\nname: s\n---\n")
        secret = tmp_path / "outside-secret.txt"
        secret.write_text("jwt-signing-key")
        return clone, skill, secret

    def test_internal_symlink_allowed(self, tmp_path):
        clone, skill, _ = self._make_clone(tmp_path)
        (skill / "helper.md").write_text("ok")
        (skill / "link.md").symlink_to(skill / "helper.md")
        _reject_escaping_symlinks(skill, clone)  # no raise

    def test_escaping_symlink_rejected(self, tmp_path):
        clone, skill, secret = self._make_clone(tmp_path)
        (skill / "steal.txt").symlink_to(secret)
        with pytest.raises(SkillError, match="symlink escaping"):
            _reject_escaping_symlinks(skill, clone)

    def test_escaping_dir_symlink_rejected(self, tmp_path):
        clone, skill, _ = self._make_clone(tmp_path)
        (skill / "data").symlink_to(tmp_path)
        with pytest.raises(SkillError, match="symlink escaping"):
            _reject_escaping_symlinks(skill, clone)
