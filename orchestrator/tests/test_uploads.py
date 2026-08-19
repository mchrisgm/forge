"""Chat attachment uploads: magic-byte sniffing, the size cap, per-user
ownership, deletion, filename sanitization, and text extraction/truncation —
at the service level and through the multipart API endpoint."""

from pathlib import Path

import pytest
from fastapi import HTTPException

from app import config
from app import db as db_module
from app.models import Upload, User
from app.services import uploads

pytestmark = pytest.mark.usefixtures("db_ready")

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 24
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 24
GIF = b"GIF89a" + b"\x00" * 24
PDF = b"%PDF-1.4\nnot really a document"
WEBP = b"RIFF\x24\x00\x00\x00WEBPVP8 " + b"\x00" * 12
WAV = b"RIFF\x24\x00\x00\x00WAVE" + b"\x00" * 12


class FakeUploadFile:
    """The slice of fastapi.UploadFile that save_upload touches."""

    def __init__(self, data: bytes, filename: str = "file.bin", content_type: str = ""):
        self._data = data
        self.filename = filename
        self.content_type = content_type

    async def read(self) -> bytes:
        return self._data


def make_user(username: str = "uploader") -> int:
    with db_module.write_session() as db:
        user = User(username=username)
        db.add(user)
        db.flush()
        return user.id


async def save(
    user_id: int, data: bytes, filename: str = "file.bin", content_type: str = ""
) -> Upload:
    return await uploads.save_upload(
        user_id, FakeUploadFile(data, filename, content_type)
    )


# ── sniffing ────────────────────────────────────────────────────────────────


class TestSniffing:
    @pytest.mark.parametrize(
        ("data", "filename", "declared", "mime", "kind"),
        [
            (PNG, "shot.png", "application/octet-stream", "image/png", "image"),
            (JPEG, "photo.whatever", "", "image/jpeg", "image"),
            (GIF, "anim.gif", "", "image/gif", "image"),
            (WEBP, "pic.webp", "", "image/webp", "image"),
            (PDF, "doc.pdf", "", "application/pdf", "pdf"),
            # Extension decides for text-ish files without magic bytes.
            (b"print('hi')\n", "script.py", "text/x-python", "text/x-python", "text"),
            (b"hello", "notes.txt", "", "text/plain", "text"),
            # Declared mime is trusted only when nothing else matches.
            (b"\x00\x01binary", "blob.img", "image/png", "image/png", "image"),
            (b"\x00\x01binary", "blob.xyz", "application/pdf", "application/pdf", "pdf"),
            (b"\x00\x01binary", "blob.xyz", "", "application/octet-stream", "other"),
            # RIFF that is not WEBP must not be mistaken for an image.
            (WAV, "sound.wav", "", "application/octet-stream", "other"),
        ],
    )
    async def test_magic_bytes_beat_extension_beat_declared_mime(
        self, data, filename, declared, mime, kind
    ):
        upload = await save(make_user(), data, filename, declared)
        assert (upload.mime, upload.kind) == (mime, kind)
        assert upload.size_bytes == len(data)

    async def test_file_lands_in_the_users_directory(self):
        user_id = make_user()
        upload = await save(user_id, PNG, "shot.png")
        path = Path(upload.path)
        assert path.is_file()
        assert path.read_bytes() == PNG
        settings = config.get_settings()
        assert path.parent == Path(settings.uploads_dir) / str(user_id)
        assert path.name == f"{upload.id}-shot.png"


# ── validation & size cap ───────────────────────────────────────────────────


class TestValidation:
    async def test_oversized_upload_is_413(self, monkeypatch):
        monkeypatch.setenv("FORGE_UPLOAD_MAX_MB", "1")
        config.get_settings.cache_clear()
        user_id = make_user()
        with pytest.raises(HTTPException) as excinfo:
            await save(user_id, b"x" * (1024 * 1024 + 1), "big.bin")
        assert excinfo.value.status_code == 413
        # Nothing was stored for the rejected file.
        user_dir = Path(config.get_settings().uploads_dir) / str(user_id)
        assert not user_dir.exists() or list(user_dir.iterdir()) == []

    async def test_at_the_limit_is_accepted(self, monkeypatch):
        monkeypatch.setenv("FORGE_UPLOAD_MAX_MB", "1")
        config.get_settings.cache_clear()
        upload = await save(make_user(), b"x" * 1024 * 1024, "exact.bin")
        assert upload.size_bytes == 1024 * 1024

    async def test_empty_file_is_400(self):
        with pytest.raises(HTTPException) as excinfo:
            await save(make_user(), b"", "empty.txt")
        assert excinfo.value.status_code == 400


# ── ownership ───────────────────────────────────────────────────────────────


class TestOwnership:
    async def test_get_owned_returns_the_owners_file(self):
        user_id = make_user()
        upload = await save(user_id, PNG, "mine.png")
        assert uploads.get_owned(upload.id, user_id).id == upload.id

    async def test_get_owned_is_404_for_another_user(self):
        owner = make_user("owner")
        stranger = make_user("stranger")
        upload = await save(owner, PNG, "mine.png")
        with pytest.raises(HTTPException) as excinfo:
            uploads.get_owned(upload.id, stranger)
        assert excinfo.value.status_code == 404

    def test_get_owned_is_404_for_unknown_id(self):
        with pytest.raises(HTTPException) as excinfo:
            uploads.get_owned("no-such-upload", make_user())
        assert excinfo.value.status_code == 404


class TestDelete:
    async def test_delete_removes_file_and_row(self):
        user_id = make_user()
        upload = await save(user_id, PNG, "gone.png")
        path = Path(upload.path)
        assert path.exists()
        uploads.delete_upload(upload.id, user_id)
        assert not path.exists()
        with db_module.read_session() as db:
            assert db.get(Upload, upload.id) is None

    async def test_delete_by_another_user_is_404_and_keeps_the_file(self):
        owner = make_user("owner")
        stranger = make_user("stranger")
        upload = await save(owner, PNG, "keep.png")
        with pytest.raises(HTTPException):
            uploads.delete_upload(upload.id, stranger)
        assert Path(upload.path).exists()
        with db_module.read_session() as db:
            assert db.get(Upload, upload.id) is not None


# ── filename sanitization ───────────────────────────────────────────────────


class TestFilenameSanitization:
    async def test_path_components_and_odd_characters_are_stripped(self):
        upload = await save(make_user(), b"data", "../../we ird?.txt")
        assert upload.filename == "we_ird_.txt"
        assert "/" not in upload.filename
        # The stored path stays inside the user's upload dir.
        settings = config.get_settings()
        assert Path(upload.path).resolve().is_relative_to(
            Path(settings.uploads_dir).resolve()
        )

    async def test_empty_filename_becomes_file(self):
        upload = await save(make_user(), b"data", "")
        assert upload.filename == "file"

    async def test_long_names_are_truncated(self):
        upload = await save(make_user(), b"data", "x" * 300 + ".txt")
        assert len(upload.filename) == 120


# ── text extraction ─────────────────────────────────────────────────────────


class TestTextContent:
    async def test_text_files_are_read_and_truncated(self):
        upload = await save(make_user(), b"A" * 20000, "big.txt")
        text = uploads.text_content(upload)
        assert len(text) == uploads.MAX_INLINE_CHARS
        assert set(text) == {"A"}

    async def test_images_yield_no_text_but_a_data_uri(self):
        upload = await save(make_user(), PNG, "shot.png")
        assert uploads.text_content(upload) == ""
        uri = uploads.image_data_uri(upload)
        assert uri is not None and uri.startswith("data:image/png;base64,")

    async def test_non_images_have_no_data_uri(self):
        upload = await save(make_user(), b"words", "notes.txt")
        assert uploads.image_data_uri(upload) is None

    async def test_corrupt_pdf_degrades_to_empty(self):
        upload = await save(make_user(), PDF, "broken.pdf")
        assert upload.kind == "pdf"
        assert uploads.text_content(upload) == ""

    async def test_missing_file_on_disk_is_empty(self):
        upload = await save(make_user(), b"gone soon", "notes.txt")
        Path(upload.path).unlink()
        assert uploads.text_content(upload) == ""


# ── the multipart API endpoint ──────────────────────────────────────────────


class TestFilesApi:
    def test_upload_list_fetch_delete_roundtrip(self, api, auth_headers):
        resp = api.post(
            "/api/files",
            files={"file": ("shot.png", PNG, "image/png")},
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        meta = resp.json()
        assert meta["filename"] == "shot.png"
        assert meta["kind"] == "image"
        assert meta["mime"] == "image/png"
        assert meta["size_bytes"] == len(PNG)

        listed = api.get("/api/files", headers=auth_headers).json()
        assert [f["id"] for f in listed] == [meta["id"]]

        fetched = api.get(f"/api/files/{meta['id']}", headers=auth_headers)
        assert fetched.status_code == 200
        assert fetched.content == PNG
        assert fetched.headers["content-type"] == "image/png"

        assert (
            api.delete(f"/api/files/{meta['id']}", headers=auth_headers).json()
            == {"ok": True}
        )
        assert api.get("/api/files", headers=auth_headers).json() == []
        assert (
            api.get(f"/api/files/{meta['id']}", headers=auth_headers).status_code
            == 404
        )

    def test_files_are_scoped_per_user(self, api, auth_headers, second_user_headers):
        meta = api.post(
            "/api/files",
            files={"file": ("secret.txt", b"my diary", "text/plain")},
            headers=auth_headers,
        ).json()

        # The other user cannot list, fetch, or delete it.
        assert api.get("/api/files", headers=second_user_headers).json() == []
        assert (
            api.get(f"/api/files/{meta['id']}", headers=second_user_headers).status_code
            == 404
        )
        assert (
            api.delete(
                f"/api/files/{meta['id']}", headers=second_user_headers
            ).status_code
            == 404
        )
        # And it is still there for the owner.
        assert (
            api.get(f"/api/files/{meta['id']}", headers=auth_headers).status_code
            == 200
        )

    def test_upload_requires_auth(self, api):
        resp = api.post("/api/files", files={"file": ("a.txt", b"hi", "text/plain")})
        assert resp.status_code == 401


# ── generated files (chat image generation) ─────────────────────────────────


class TestSaveGenerated:
    def test_png_magic_sets_mime_kind_and_slugged_filename(self):
        user_id = make_user()
        upload = uploads.save_generated(user_id, PNG, "A red FOX  jumps!")
        assert upload.mime == "image/png"
        assert upload.kind == "image"
        assert upload.filename == "a-red-fox-jumps.png"
        assert upload.generated is True
        assert upload.prompt == "A red FOX  jumps!"
        assert upload.size_bytes == len(PNG)
        path = Path(upload.path)
        assert path.read_bytes() == PNG
        assert path.parent == Path(config.get_settings().uploads_dir) / str(user_id)

    def test_sniffed_magic_beats_the_declared_mime(self):
        upload = uploads.save_generated(make_user(), JPEG, "a fox", mime="image/png")
        assert upload.mime == "image/jpeg"
        assert upload.filename.endswith(".jpg")

    def test_unknown_bytes_get_a_bin_extension(self):
        upload = uploads.save_generated(make_user(), b"\x00\x01data", "blob", mime="")
        assert upload.kind == "other"
        assert upload.filename == "blob.bin"

    def test_symbol_only_prompt_falls_back_to_generated(self):
        upload = uploads.save_generated(make_user(), PNG, "???!!!")
        assert upload.filename == "generated.png"

    def test_long_prompts_truncate_stem_and_stored_prompt(self):
        upload = uploads.save_generated(make_user(), PNG, "x" * 3000)
        assert upload.filename == "x" * 48 + ".png"
        assert len(upload.prompt) == 2000

    def test_empty_data_is_502(self):
        with pytest.raises(HTTPException) as excinfo:
            uploads.save_generated(make_user(), b"", "a fox")
        assert excinfo.value.status_code == 502

    def test_oversize_generation_is_502(self, monkeypatch):
        monkeypatch.setenv("FORGE_UPLOAD_MAX_MB", "1")
        config.get_settings.cache_clear()
        user_id = make_user()
        with pytest.raises(HTTPException) as excinfo:
            uploads.save_generated(user_id, PNG + b"\x00" * 1024 * 1024, "big")
        assert excinfo.value.status_code == 502
        # Nothing was stored for the rejected generation.
        user_dir = Path(config.get_settings().uploads_dir) / str(user_id)
        assert not user_dir.exists() or list(user_dir.iterdir()) == []

    def test_served_to_the_owner_and_hidden_from_others(
        self, api, auth_headers, second_user_headers
    ):
        owner_id = api.get("/api/users/me", headers=auth_headers).json()["id"]
        upload = uploads.save_generated(owner_id, PNG, "a fox")

        fetched = api.get(f"/api/files/{upload.id}", headers=auth_headers)
        assert fetched.status_code == 200
        assert fetched.content == PNG
        assert fetched.headers["content-type"] == "image/png"
        assert [f["id"] for f in api.get("/api/files", headers=auth_headers).json()] == [
            upload.id
        ]

        assert (
            api.get(f"/api/files/{upload.id}", headers=second_user_headers).status_code
            == 404
        )
        assert api.get("/api/files", headers=second_user_headers).json() == []
