"""uploads.save_generated_text: markdown Upload rows for Forge-generated text
(web page reads) — filename derivation from the source URL, storage location,
row metadata, the size cap, and interop with text_content/get_owned/delete."""

from pathlib import Path

import pytest
from fastapi import HTTPException

from app import config
from app import db as db_module
from app.models import User
from app.services import uploads

pytestmark = pytest.mark.usefixtures("db_ready")

TEXT = "# Example Domain\n\nSome extracted markdown.\n"
URL = "https://example.com/article"


def make_user(username: str = "reader") -> int:
    with db_module.write_session() as db:
        user = User(username=username)
        db.add(user)
        db.flush()
        return user.id


class TestSaveGeneratedText:
    def test_row_metadata_and_file_contents(self):
        user_id = make_user()
        upload = uploads.save_generated_text(user_id, TEXT, URL)
        assert upload.user_id == user_id
        assert upload.kind == "text"
        assert upload.mime == "text/markdown"
        assert upload.generated is True
        assert upload.prompt == URL
        assert upload.filename == "example-com-article.md"
        assert upload.size_bytes == len(TEXT.encode())
        path = Path(upload.path)
        assert path.read_text() == TEXT
        # Stored inside the user's uploads directory, like every other upload.
        user_dir = Path(config.get_settings().uploads_dir) / str(user_id)
        assert path.parent == user_dir

    def test_filename_slugs_scheme_case_and_query(self):
        upload = uploads.save_generated_text(
            make_user(), TEXT, "HTTPS://Example.COM/Some/Page?q=1&x=2"
        )
        assert upload.filename == "example-com-some-page-q-1-x-2.md"

    def test_long_urls_truncate_the_stem_but_keep_the_full_prompt(self):
        url = "https://example.com/" + "segment/" * 40
        upload = uploads.save_generated_text(make_user(), TEXT, url)
        stem = upload.filename.removesuffix(".md")
        assert len(stem) == 48
        assert upload.prompt == url

    def test_symbol_only_url_falls_back_to_page(self):
        upload = uploads.save_generated_text(make_user(), TEXT, "https://…/")
        assert upload.filename == "page.md"

    def test_blank_text_is_502_and_stores_nothing(self):
        user_id = make_user()
        with pytest.raises(HTTPException) as excinfo:
            uploads.save_generated_text(user_id, "   \n  ", URL)
        assert excinfo.value.status_code == 502
        user_dir = Path(config.get_settings().uploads_dir) / str(user_id)
        assert not user_dir.exists() or list(user_dir.iterdir()) == []

    def test_oversized_text_is_502(self, monkeypatch):
        monkeypatch.setenv("FORGE_UPLOAD_MAX_MB", "1")
        config.get_settings.cache_clear()
        with pytest.raises(HTTPException) as excinfo:
            uploads.save_generated_text(make_user(), "x" * (1024 * 1024 + 1), URL)
        assert excinfo.value.status_code == 502

    def test_behaves_like_any_text_upload_afterwards(self):
        user_id = make_user()
        other_id = make_user("other")
        upload = uploads.save_generated_text(user_id, TEXT, URL)
        # Prompt inlining reads it as text; ownership and deletion apply.
        assert uploads.text_content(upload) == TEXT
        assert uploads.get_owned(upload.id, user_id).id == upload.id
        with pytest.raises(HTTPException):
            uploads.get_owned(upload.id, other_id)
        uploads.delete_upload(upload.id, user_id)
        assert not Path(upload.path).exists()
