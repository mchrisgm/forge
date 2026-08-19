"""Model registry — suggest-only HF scan + scoring (PLAN §6.4).

Score = 0.35·trend + 0.25·recency(half-life 60d) + 0.25·coding_signal + 0.15·fit_quality.
Pure scoring helpers are separated from the network scan so tests can drive
them with fixture JSON.
"""

import json
import logging
import math
import re
from datetime import UTC, datetime
from typing import Any

from sqlmodel import select

from ..config import get_settings
from ..db import read_session, write_session
from ..models import ModelEntry, Suggestion
from .events import bus
from .fit_rules import FIT_QUALITY, Budgets, assign_lane

log = logging.getLogger(__name__)

QUANTIZER_ORGS = ("bartowski", "unsloth", "mradermacher")

_CODING_RE = re.compile(
    r"coder|[-_.]code[-_.]|codegen|starcoder|deepseek[-_]?coder|devstral|codestral"
    r"|humaneval|swe[-_]?bench|instruct",
    re.IGNORECASE,
)
_STRONG_CODING_RE = re.compile(r"coder|codestral|devstral|swe[-_]?bench", re.IGNORECASE)
_PARAMS_RE = re.compile(r"(\d+(?:\.\d+)?)\s*[bB](?![a-zA-Z])")
_MOE_RE = re.compile(r"[aA]\d+(?:\.\d+)?[bB]|mixtral|moe", re.IGNORECASE)


def trend_rank_norm(rank: int, total: int) -> float:
    """1.0 for the top-trending model, linearly to ~0 for the last."""
    if total <= 1:
        return 1.0
    return max(0.0, 1.0 - rank / (total - 1))


def recency_decay(
    created_at: datetime, now: datetime | None = None, half_life_days: float = 60
) -> float:
    now = now or datetime.now(UTC)
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=UTC)
    age_days = max(0.0, (now - created_at).total_seconds() / 86400)
    return math.pow(0.5, age_days / half_life_days)


def coding_signal(model_id: str, tags: list[str]) -> float:
    haystack = model_id + " " + " ".join(tags)
    if _STRONG_CODING_RE.search(haystack):
        return 1.0
    if _CODING_RE.search(haystack):
        return 0.6
    return 0.0


def estimate_params_b(model_id: str, safetensors_total: int | None = None) -> float:
    if safetensors_total:
        return round(safetensors_total / 1e9, 1)
    matches = _PARAMS_RE.findall(model_id)
    if matches:
        # For MoE names like "30B-A3B" the first number is total params.
        return float(matches[0])
    return 0.0


def is_moe(model_id: str, tags: list[str]) -> bool:
    return bool(_MOE_RE.search(model_id + " " + " ".join(tags)))


def score_candidate(
    *,
    rank: int,
    total: int,
    created_at: datetime,
    model_id: str,
    tags: list[str],
    lane: str | None,
    now: datetime | None = None,
) -> dict[str, Any]:
    trend = trend_rank_norm(rank, total)
    recency = recency_decay(created_at, now)
    coding = coding_signal(model_id, tags)
    fit = FIT_QUALITY.get(lane or "", 0.0)
    score = 0.35 * trend + 0.25 * recency + 0.25 * coding + 0.15 * fit
    return {
        "trend": round(trend, 3),
        "recency": round(recency, 3),
        "coding_signal": round(coding, 3),
        "fit": round(fit, 3),
        "lane": lane,
        "score": round(score, 3),
    }


def _find_gguf(
    api: Any, model_id: str, token: str | None
) -> tuple[str | None, str | None, float]:
    """Find a Q4_K_M-ish GGUF for a repo: in the repo itself, an author -GGUF
    sibling, or a known quantizer org. Returns (repo, filename, size_gb)."""
    name = model_id.split("/")[-1]
    author = model_id.split("/")[0] if "/" in model_id else ""
    candidates = [model_id, f"{author}/{name}-GGUF"] + [
        f"{org}/{name}-GGUF" for org in QUANTIZER_ORGS
    ]
    for repo in candidates:
        try:
            if repo != model_id and not api.repo_exists(repo, token=token):
                continue
            info = api.model_info(repo, files_metadata=True, token=token)
        except Exception:
            continue
        ggufs = [
            s
            for s in (info.siblings or [])
            if s.rfilename.lower().endswith(".gguf")
            and s.size
            # Split quants (-00001-of-00003.gguf): a single part is unloadable
            # and its lone size would poison lane assignment; skip them (their
            # total exceeds the offload budget anyway). Also skip multimodal
            # projector files.
            and not re.search(r"-\d{5}-of-\d{5}\.gguf$", s.rfilename.lower())
            and not s.rfilename.rsplit("/", 1)[-1].lower().startswith("mmproj")
        ]
        if not ggufs:
            continue
        preferred = [s for s in ggufs if "q4_k_m" in s.rfilename.lower()] or ggufs
        best = min(preferred, key=lambda s: s.size or 0)
        return repo, best.rfilename, round((best.size or 0) / 1024**3, 2)
    return None, None, 0.0


def _has_awq(model_id: str, tags: list[str]) -> bool:
    return "awq" in [t.lower() for t in tags] or "awq" in model_id.lower()


def scan(limit: int | None = None) -> dict[str, Any]:
    """Query HF for trending + most-downloaded text-generation models, score
    them against this hardware, and upsert Suggestion rows. Runs in a worker
    thread (blocking network I/O)."""
    from huggingface_hub import HfApi

    settings = get_settings()
    limit = limit or settings.registry_scan_limit
    token = settings.hf_token or None
    api = HfApi(token=token)

    seen: dict[str, dict[str, Any]] = {}
    for sort_key in ("trendingScore", "downloads"):
        try:
            listing = list(
                api.list_models(
                    pipeline_tag="text-generation",
                    sort=sort_key,
                    direction=-1,
                    limit=limit,
                )
            )
        except Exception as exc:
            log.warning("HF list_models(%s) failed: %s", sort_key, exc)
            continue
        total = len(listing)
        for rank, m in enumerate(listing):
            entry = seen.setdefault(
                m.id,
                {"model": m, "best_rank": rank, "total": total},
            )
            entry["best_rank"] = min(entry["best_rank"], rank)

    with read_session() as db:
        catalog_repos = set(db.exec(select(ModelEntry.hf_repo)).all())
        existing = {s.hf_repo: s for s in db.exec(select(Suggestion)).all()}

    budgets = Budgets(settings.vram_budget_gb, settings.ram_offload_budget_gb)
    created, skipped, detail_budget = 0, 0, 40

    ranked = sorted(seen.values(), key=lambda e: e["best_rank"])
    for entry in ranked:
        m = entry["model"]
        tags = list(m.tags or [])
        # Cheap prefilter before spending API calls on artifact discovery.
        if coding_signal(m.id, tags) == 0.0:
            skipped += 1
            continue
        if m.id in catalog_repos or (m.id in existing and existing[m.id].dismissed):
            continue
        if detail_budget <= 0:
            break
        detail_budget -= 1

        params_b = estimate_params_b(
            m.id, getattr(getattr(m, "safetensors", None), "total", None)
        )
        gguf_repo, gguf_file, gguf_size = _find_gguf(api, m.id, token)
        has_awq = _has_awq(m.id, tags)
        lane = assign_lane(
            params_b, gguf_size or None, has_awq, is_moe(m.id, tags), budgets=budgets
        )
        if lane is None or (lane == "airllm" and params_b > 70):
            continue

        created_at = getattr(m, "created_at", None) or datetime.now(UTC)
        breakdown = score_candidate(
            rank=entry["best_rank"],
            total=entry["total"],
            created_at=created_at,
            model_id=m.id,
            tags=tags,
            lane=lane,
        )
        if breakdown["score"] < settings.registry_score_threshold:
            continue
        breakdown.update(
            {
                "params_b": params_b,
                "is_moe": is_moe(m.id, tags),
                "gguf_repo": gguf_repo,
                "gguf_file": gguf_file,
                "gguf_size_gb": gguf_size,
                "has_awq": has_awq,
            }
        )
        with write_session() as db:
            existing_row = db.exec(
                select(Suggestion).where(Suggestion.hf_repo == m.id)
            ).first()
            if existing_row:
                existing_row.reason = json.dumps(breakdown)
                db.add(existing_row)
            else:
                db.add(Suggestion(hf_repo=m.id, reason=json.dumps(breakdown)))
                created += 1

    bus.publish("registry.scan_done", {"new_suggestions": created})
    return {
        "new_suggestions": created,
        "considered": len(seen),
        "skipped_no_coding_signal": skipped,
    }


# ── on-demand Hub search (Models page search box) ───────────────────────────

SEARCH_PIPELINES = {"text": "text-generation", "image": "text-to-image"}


def search_hub(query: str, kind: str = "text", limit: int = 20) -> list[dict[str, Any]]:
    """Search the Hub for a specific model by name. Blocking — run in a
    worker thread. `kind` picks the pipeline: text (chat/code) or image
    (text-to-image for the imagegen lane)."""
    from huggingface_hub import HfApi

    settings = get_settings()
    api = HfApi(token=settings.hf_token or None)
    listing = list(
        api.list_models(
            search=query,
            pipeline_tag=SEARCH_PIPELINES[kind],
            sort="downloads",
            direction=-1,
            limit=max(1, min(limit, 50)),
        )
    )
    with read_session() as db:
        catalog_repos = set(db.exec(select(ModelEntry.hf_repo)).all())
        # add() names entries after the SEARCHED repo but may store a resolved
        # quantizer repo in hf_repo (GGUF rewrite) — match display names too,
        # or added models would keep showing an Add button.
        catalog_names = set(db.exec(select(ModelEntry.display_name)).all())
    results = []
    for m in listing:
        tags = list(m.tags or [])
        created_at = getattr(m, "created_at", None)
        results.append(
            {
                "hf_repo": m.id,
                "downloads": int(getattr(m, "downloads", 0) or 0),
                "likes": int(getattr(m, "likes", 0) or 0),
                "tags": tags[:10],
                "gated": bool(getattr(m, "gated", False)),
                "created_at": created_at.isoformat() if created_at else None,
                "params_b": estimate_params_b(m.id) if kind == "text" else 0.0,
                "in_catalog": m.id in catalog_repos
                or m.id.split("/")[-1] in catalog_names,
            }
        )
    return results


def is_diffusers_repo(hf_repo: str) -> bool:
    """True when the repo is diffusers-format (model_index.json present) —
    the only layout the imagegen server can load. Blocking."""
    from huggingface_hub import HfApi

    settings = get_settings()
    try:
        files = HfApi(token=settings.hf_token or None).list_repo_files(hf_repo)
    except Exception:
        return False
    return "model_index.json" in files


def resolve_text_candidate(hf_repo: str) -> dict[str, Any]:
    """Artifact discovery + lane assignment for one repo the user picked from
    search — the same pipeline scan() runs per suggestion. Blocking."""
    from huggingface_hub import HfApi

    settings = get_settings()
    token = settings.hf_token or None
    api = HfApi(token=token)
    info = api.model_info(hf_repo)
    tags = list(info.tags or [])
    params_b = estimate_params_b(
        hf_repo, getattr(getattr(info, "safetensors", None), "total", None)
    )
    gguf_repo, gguf_file, gguf_size = _find_gguf(api, hf_repo, token)
    budgets = Budgets(settings.vram_budget_gb, settings.ram_offload_budget_gb)
    lane = assign_lane(
        params_b,
        gguf_size or None,
        _has_awq(hf_repo, tags),
        is_moe(hf_repo, tags),
        budgets=budgets,
    )
    return {
        "lane": lane,
        "params_b": params_b,
        "is_moe": is_moe(hf_repo, tags),
        "gguf_repo": gguf_repo,
        "gguf_file": gguf_file,
        "gguf_size_gb": gguf_size,
    }


def snapshot_size_gb(hf_repo: str) -> float:
    """Total weight size of a repo snapshot (safetensors preferred), GiB."""
    from huggingface_hub import HfApi

    settings = get_settings()
    try:
        info = HfApi(token=settings.hf_token or None).model_info(
            hf_repo, files_metadata=True
        )
    except Exception:
        return 0.0
    siblings = info.siblings or []
    total = sum(
        s.size or 0 for s in siblings if s.rfilename.endswith(".safetensors")
    ) or sum(s.size or 0 for s in siblings if s.size)
    return round(total / 1024**3, 2)
