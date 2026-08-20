"""Thin GitHub REST helpers for the signed-in user's account.

Used by the session-creation repo picker: list the repos (public AND
private) the caller's token can see, newest activity first. Works with an
OAuth token or a pasted PAT — both live in the github connector's token
slot and both authenticate the same way.
"""

from __future__ import annotations

import httpx

API = "https://api.github.com"
_PAGE_SIZE = 100
_MAX_PAGES = 3  # 300 repos is plenty for a picker; the query filter narrows


class GitHubApiError(Exception):
    def __init__(self, detail: str, status_code: int = 502) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


def _headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


async def list_repos(token: str, query: str = "") -> list[dict]:
    """The caller's repos across owner/collaborator/org grants, most recently
    pushed first, optionally substring-filtered by full name."""
    repos: list[dict] = []
    try:
        async with httpx.AsyncClient(timeout=20) as http:
            for page in range(1, _MAX_PAGES + 1):
                resp = await http.get(
                    f"{API}/user/repos",
                    headers=_headers(token),
                    params={
                        "per_page": _PAGE_SIZE,
                        "page": page,
                        "sort": "pushed",
                        "direction": "desc",
                        "affiliation": "owner,collaborator,organization_member",
                    },
                )
                if resp.status_code == 401:
                    raise GitHubApiError(
                        "GitHub rejected the stored token — reconnect the "
                        "GitHub connector",
                        401,
                    )
                resp.raise_for_status()
                batch = resp.json()
                repos.extend(batch)
                if len(batch) < _PAGE_SIZE:
                    break
    except httpx.HTTPError as exc:
        raise GitHubApiError(f"GitHub is unreachable: {exc}") from exc

    needle = query.strip().lower()
    results = []
    for repo in repos:
        full_name = str(repo.get("full_name", ""))
        if needle and needle not in full_name.lower():
            continue
        results.append(
            {
                "full_name": full_name,
                "private": bool(repo.get("private")),
                "default_branch": repo.get("default_branch") or "main",
                "description": (repo.get("description") or "")[:200],
                "pushed_at": repo.get("pushed_at"),
                "html_url": repo.get("html_url", ""),
                # What the session clones — the entrypoint's credential store
                # (fed by the same token) authenticates it for private repos.
                "clone_url": f"https://github.com/{full_name}.git",
            }
        )
    return results
