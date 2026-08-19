"""File & git operations inside session containers via docker exec (PLAN §6.1).

The orchestrator never touches workspace volumes directly beyond
creation/cleanup — everything here is `docker exec` with list-args. User input
is never interpolated into a shell string: paths are shlex-quoted and file
contents travel base64-encoded.
"""

import io
import posixpath
import tarfile
import time
from dataclasses import dataclass

from ..models import Session
from . import docker_util
from .session_manager import container_name

WORKSPACE = "/workspace"
MAX_FILE_BYTES = 2 * 1024 * 1024
SESSION_UID = 1000  # the "forge" user inside session-runner containers


class ExecError(Exception):
    def __init__(self, detail: str, status_code: int = 400):
        self.detail = detail
        self.status_code = status_code
        super().__init__(detail)


def safe_workspace_path(rel_path: str) -> str:
    """Resolve a client-supplied path inside /workspace or raise."""
    rel_path = rel_path.lstrip("/")
    joined = posixpath.normpath(posixpath.join(WORKSPACE, rel_path))
    if joined != WORKSPACE and not joined.startswith(WORKSPACE + "/"):
        raise ExecError("path escapes the workspace", 400)
    return joined


def _container(session: Session):
    try:
        return docker_util.client().containers.get(container_name(session.id))
    except Exception as exc:
        raise ExecError(f"session container unavailable: {exc}", 409) from exc


def _run_in_container(
    session: Session, cmd: list[str], workdir: str = WORKSPACE
) -> tuple[int, str]:
    container = _container(session)
    if container.status != "running":
        raise ExecError("session is not running — start it first", 409)
    code, output = container.exec_run(cmd, workdir=workdir, demux=False)
    return code, output.decode(errors="replace") if output else ""


@dataclass
class DirEntry:
    name: str
    type: str  # "file" | "dir" | "link" | "other"
    size: int


def list_dir(session: Session, rel_path: str = "") -> list[DirEntry]:
    target = safe_workspace_path(rel_path)
    code, out = _run_in_container(
        session,
        [
            "find", target, "-maxdepth", "1", "-mindepth", "1",
            "-printf", "%y\\t%s\\t%f\\n",
        ],
    )
    if code != 0:
        raise ExecError(f"cannot list {rel_path or '/'}: {out.strip()[:300]}", 404)
    entries: list[DirEntry] = []
    for line in out.splitlines():
        parts = line.split("\t", 2)
        if len(parts) != 3:
            continue
        kind_char, size, name = parts
        kind = {"f": "file", "d": "dir", "l": "link"}.get(kind_char, "other")
        entries.append(DirEntry(name=name, type=kind, size=int(size or 0)))
    entries.sort(key=lambda e: (e.type != "dir", e.name.lower()))
    return entries


def read_file(session: Session, rel_path: str) -> str:
    target = safe_workspace_path(rel_path)
    code, out = _run_in_container(session, ["stat", "-c", "%s", target])
    if code != 0:
        raise ExecError("file not found", 404)
    try:
        size = int(out.strip())
    except ValueError as exc:
        raise ExecError("cannot stat file", 500) from exc
    if size > MAX_FILE_BYTES:
        raise ExecError(f"file too large to view ({size} bytes)", 413)
    code, out = _run_in_container(session, ["cat", target])
    if code != 0:
        raise ExecError("cannot read file", 500)
    return out


def write_file(session: Session, rel_path: str, content: str) -> None:
    """Write via the Docker copy API (put_archive). Shell-free — content never
    touches argv (Linux caps a single argv string at ~128 KiB) and there is
    nothing to quote. Ownership is set to the session's non-root user so the
    agent can keep editing files the UI wrote."""
    target = safe_workspace_path(rel_path)
    data = content.encode()
    if len(data) > MAX_FILE_BYTES:
        raise ExecError("content too large", 413)

    arcname = target[len(WORKSPACE) + 1 :] if target != WORKSPACE else ""
    if not arcname:
        raise ExecError("path names the workspace root", 400)

    buf = io.BytesIO()
    now = int(time.time())
    with tarfile.open(fileobj=buf, mode="w") as tar:
        # Explicit parent-dir entries so ownership/permissions are right even
        # when the UI writes into a directory that does not exist yet.
        parts = arcname.split("/")[:-1]
        for i in range(1, len(parts) + 1):
            dir_info = tarfile.TarInfo("/".join(parts[:i]))
            dir_info.type = tarfile.DIRTYPE
            dir_info.mode = 0o755
            dir_info.uid = dir_info.gid = SESSION_UID
            dir_info.mtime = now
            tar.addfile(dir_info)
        info = tarfile.TarInfo(arcname)
        info.size = len(data)
        info.mode = 0o644
        info.uid = info.gid = SESSION_UID
        info.mtime = now
        tar.addfile(info, io.BytesIO(data))

    container = _container(session)
    if container.status != "running":
        raise ExecError("session is not running — start it first", 409)
    try:
        ok = container.put_archive(WORKSPACE, buf.getvalue())
    except Exception as exc:
        raise ExecError(f"write failed: {exc}", 500) from exc
    if not ok:
        raise ExecError("write failed", 500)


def git(session: Session, args: list[str]) -> tuple[int, str]:
    return _run_in_container(session, ["git", "-C", WORKSPACE, *args])


def git_status(session: Session) -> dict:
    code, out = git(session, ["status", "--porcelain=v1", "--branch"])
    if code != 0:
        raise ExecError(f"git status failed: {out.strip()[:300]}", 400)
    branch, changes = "", []
    for line in out.splitlines():
        if line.startswith("##"):
            branch = line[3:].strip()
        elif line.strip():
            changes.append({"status": line[:2].strip() or "??", "path": line[3:]})
    return {"branch": branch, "changes": changes}


def git_log(session: Session, limit: int = 30) -> list[dict]:
    code, out = git(
        session, ["log", f"--max-count={limit}", "--pretty=format:%H%x09%an%x09%aI%x09%s"]
    )
    if code != 0:
        return []
    commits = []
    for line in out.splitlines():
        parts = line.split("\t", 3)
        if len(parts) == 4:
            commits.append(
                {"hash": parts[0], "author": parts[1], "date": parts[2], "subject": parts[3]}
            )
    return commits


def git_diff(session: Session, staged: bool = False) -> str:
    args = ["diff", "--stat=200", "--patch"]
    if staged:
        args.insert(1, "--cached")
    code, out = git(session, args)
    if code != 0:
        raise ExecError(f"git diff failed: {out.strip()[:300]}", 400)
    return out[:MAX_FILE_BYTES]


def git_commit(session: Session, message: str, add_all: bool = True) -> str:
    if not message.strip():
        raise ExecError("commit message required")
    if add_all:
        code, out = git(session, ["add", "-A"])
        if code != 0:
            raise ExecError(f"git add failed: {out.strip()[:300]}")
    code, out = git(session, ["commit", "-m", message])
    if code != 0:
        raise ExecError(f"git commit failed: {out.strip()[:300]}")
    return out.strip()


def git_push(session: Session) -> str:
    code, out = git(session, ["push"])
    if code != 0:
        raise ExecError(f"git push failed: {out.strip()[:500]}")
    return out.strip()
