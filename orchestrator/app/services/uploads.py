"""Chat attachments: per-user file storage under uploads_dir.

Images are passed to vision-capable models as data URIs; text-ish files are
inlined (truncated) into the prompt; PDFs get text-extracted via pypdf.
"""

import logging
import re
import uuid
from pathlib import Path

from fastapi import HTTPException, UploadFile

from ..config import get_settings
from ..db import read_session, write_session
from ..models import Upload

log = logging.getLogger(__name__)

IMAGE_MIMES = {"image/png", "image/jpeg", "image/webp", "image/gif"}
TEXT_EXTENSIONS = {
    ".txt", ".md", ".py", ".js", ".ts", ".tsx", ".jsx", ".json", ".yaml", ".yml",
    ".toml", ".csv", ".html", ".css", ".sh", ".sql", ".rs", ".go", ".java", ".c",
    ".cpp", ".h", ".rb", ".php", ".xml", ".log", ".ini", ".cfg",
}
MAX_INLINE_CHARS = 16000  # per text attachment, before the shared budget cap

_MAGIC = {
    b"\x89PNG": "image/png",
    b"\xff\xd8\xff": "image/jpeg",
    b"RIFF": "image/webp",  # + WEBP at offset 8, checked below
    b"GIF8": "image/gif",
    b"%PDF": "application/pdf",
}


def _sniff(head: bytes, filename: str, declared: str) -> tuple[str, str]:
    """(mime, kind) from magic bytes first, extension second."""
    for magic, mime in _MAGIC.items():
        if head.startswith(magic):
            if mime == "image/webp" and head[8:12] != b"WEBP":
                continue
            kind = "pdf" if mime == "application/pdf" else "image"
            return mime, kind
    ext = Path(filename).suffix.lower()
    if ext in TEXT_EXTENSIONS:
        return declared or "text/plain", "text"
    if declared in IMAGE_MIMES:
        return declared, "image"
    if declared == "application/pdf":
        return declared, "pdf"
    return declared or "application/octet-stream", "other"


def _safe_name(filename: str) -> str:
    name = Path(filename or "file").name
    return re.sub(r"[^\w.\-]", "_", name)[:120] or "file"


async def save_upload(user_id: int, file: UploadFile) -> Upload:
    settings = get_settings()
    limit = settings.upload_max_mb * 1024 * 1024
    data = await file.read()
    if len(data) > limit:
        raise HTTPException(413, f"file exceeds {settings.upload_max_mb} MB limit")
    if not data:
        raise HTTPException(400, "empty file")

    mime, kind = _sniff(data[:16], file.filename or "", file.content_type or "")
    upload_id = str(uuid.uuid4())
    safe = _safe_name(file.filename or "")
    user_dir = Path(settings.uploads_dir) / str(user_id)
    user_dir.mkdir(parents=True, exist_ok=True)
    dest = user_dir / f"{upload_id}-{safe}"
    dest.write_bytes(data)

    upload = Upload(
        id=upload_id,
        user_id=user_id,
        filename=safe,
        mime=mime,
        kind=kind,
        size_bytes=len(data),
        path=str(dest),
    )
    with write_session() as db:
        db.add(upload)
    with read_session() as db:
        return db.get(Upload, upload_id)


def get_owned(upload_id: str, user_id: int) -> Upload:
    with read_session() as db:
        upload = db.get(Upload, upload_id)
    if upload is None or upload.user_id != user_id:
        raise HTTPException(404, "file not found")
    return upload


def delete_upload(upload_id: str, user_id: int) -> None:
    upload = get_owned(upload_id, user_id)
    path = Path(upload.path)
    settings = get_settings()
    uploads_root = Path(settings.uploads_dir).resolve()
    if path.exists() and uploads_root in path.resolve().parents:
        path.unlink(missing_ok=True)
    with write_session() as db:
        row = db.get(Upload, upload_id)
        if row:
            db.delete(row)


def text_content(upload: Upload) -> str:
    """Best-effort text for prompt inlining (text files + PDFs)."""
    path = Path(upload.path)
    if not path.exists():
        return ""
    if upload.kind == "text":
        try:
            return path.read_text(encoding="utf-8", errors="replace")[:MAX_INLINE_CHARS]
        except OSError:
            return ""
    if upload.kind == "pdf":
        try:
            from pypdf import PdfReader

            reader = PdfReader(str(path))
            pages = [page.extract_text() or "" for page in reader.pages[:30]]
            return "\n".join(pages)[:MAX_INLINE_CHARS]
        except Exception as exc:
            log.warning("pdf extraction failed for %s: %s", upload.id, exc)
            return ""
    return ""


def image_data_uri(upload: Upload) -> str | None:
    import base64

    if upload.kind != "image":
        return None
    path = Path(upload.path)
    if not path.exists():
        return None
    encoded = base64.b64encode(path.read_bytes()).decode()
    return f"data:{upload.mime};base64,{encoded}"
