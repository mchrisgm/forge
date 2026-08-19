"""File & git operations inside session containers via docker exec (PLAN §6.1).

The orchestrator never touches workspace volumes directly beyond
creation/cleanup — everything here is `docker exec` with list-args. User input
is never interpolated into a shell string: paths are shlex-quoted and file
contents travel base64-encoded.
"""

import base64
import posixpath
import shlex
from dataclasses import dataclass

from ..models import Session
from . import docker_util
from .session_manager import container_name

WORKSPACE = "/workspace"
MAX_FILE_BYTES = 2 * 1024 * 1024


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
    target = safe_workspace_path(rel_path)
    encoded = base64.b64encode(content.encode()).decode()
    if len(encoded) > MAX_FILE_BYTES * 2:
        raise ExecError("content too large", 413)
    script = (
        f"mkdir -p $(dirname {shlex.quote(target)}) && "
        f"echo {shlex.quote(encoded)} | base64 -d > {shlex.quote(target)}"
    )
    code, out = _run_in_container(session, ["sh", "-c", script])
    if code != 0:
        raise ExecError(f"write failed: {out.strip()[:300]}", 500)


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
