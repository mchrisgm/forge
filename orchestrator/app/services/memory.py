"""Per-user memory engine (see docs/memory.md for the full design).

The pipeline, tuned for small local models and tight token budgets:

- **Extraction**: after each saved chat exchange, the serving model distills
  durable memories (facts / preferences / projects / episodes) as JSON *ops*
  (add / update / delete), deduplicated against the closest existing entries —
  the model sees what is already known, so memories converge instead of piling
  up.
- **Retrieval**: SQLite FTS5 BM25 over entry content (porter stemming),
  re-ranked by importance x recency x usage; pinned entries always win. If
  this SQLite lacks FTS5 the engine degrades to LIKE-term matching.
- **Budgeted injection**: retrieved entries render into one compact system
  block capped at FORGE_MEMORY_TOKEN_BUDGET (~700 tokens default); injected
  entries record usage, which feeds later ranking.
- **Compression**: long conversations roll their old turns into an
  incremental summary (previous summary + evicted turns -> new summary), so
  the live prompt stays inside FORGE_CHAT_CONTEXT_TOKENS while continuity
  survives arbitrarily long histories.
- **Consolidation & decay**: a nightly job decays unused importance, prunes
  the noise floor, and (engine permitting) merges near-duplicates.

All model calls ride whatever GPU lease is serving; with no engine loaded
every step degrades gracefully (retrieval still works, extraction waits).
Temporary chats never touch memory in either direction.
"""

import asyncio
import json
import logging
import math
import re
from datetime import UTC, datetime

from sqlalchemy import text as sql_text
from sqlmodel import select

from ..config import get_settings
from ..db import fts_available, get_engine, read_session, write_session
from ..models import ChatMessage, Conversation, MemoryEntry, MemoryKind, User

log = logging.getLogger(__name__)

MAX_ENTRIES_PER_USER = 400
MAX_ENTRY_CHARS = 500
RETRIEVE_LIMIT = 8
DECAY_HALF_LIFE_DAYS = 90.0
PRUNE_IMPORTANCE_FLOOR = 0.15


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


# ── retrieval ───────────────────────────────────────────────────────────────


def _fts_terms(query: str) -> str:
    terms = re.findall(r"[A-Za-z0-9_]{3,}", query.lower())[:12]
    return " OR ".join(dict.fromkeys(terms))


def _candidates(user_id: int, query: str, limit: int = 32) -> list[tuple[MemoryEntry, float]]:
    """(entry, match_score in 0..1) — FTS5 BM25 when available, LIKE fallback."""
    terms = _fts_terms(query)
    results: list[tuple[MemoryEntry, float]] = []
    if terms and fts_available():
        engine = get_engine()
        with engine.connect() as conn:
            try:
                rows = conn.execute(
                    sql_text(
                        "SELECT m.id, bm25(memory_fts) AS rank FROM memory_fts "
                        "JOIN memoryentry m ON m.id = memory_fts.rowid "
                        "WHERE memory_fts MATCH :q AND m.user_id = :uid "
                        "ORDER BY rank LIMIT :lim"
                    ),
                    {"q": terms, "uid": user_id, "lim": limit},
                ).all()
            except Exception:
                rows = []
        if rows:
            ids = [r[0] for r in rows]
            # bm25 is lower-is-better; normalize to 0..1
            ranks = {r[0]: float(r[1]) for r in rows}
            best, worst = min(ranks.values()), max(ranks.values())
            span = (worst - best) or 1.0
            with read_session() as db:
                for entry_id in ids:
                    entry = db.get(MemoryEntry, entry_id)
                    if entry is not None:
                        score = 1.0 - (ranks[entry_id] - best) / span
                        results.append((entry, 0.3 + 0.7 * score))
            return results

    # Fallback: keyword LIKE scan (small tables, fine for one LAN's users)
    words = [w for w in re.findall(r"[A-Za-z0-9_]{4,}", query.lower())][:8]
    with read_session() as db:
        entries = db.exec(
            select(MemoryEntry).where(MemoryEntry.user_id == user_id)
        ).all()
    for entry in entries:
        content = entry.content.lower()
        hits = sum(1 for w in words if w in content)
        if hits:
            results.append((entry, min(1.0, 0.3 + 0.2 * hits)))
    return results[:limit]


def _rank(entry: MemoryEntry, match: float, now: datetime) -> float:
    updated = (
        entry.updated_at
        if entry.updated_at.tzinfo
        else entry.updated_at.replace(tzinfo=UTC)
    )
    age_days = max(0.0, (now - updated).total_seconds() / 86400)
    recency = math.pow(0.5, age_days / DECAY_HALF_LIFE_DAYS)
    usage = min(1.0, 0.2 + 0.1 * entry.use_count)
    return match * (0.5 + 0.5 * min(entry.importance, 2.0)) * (
        0.6 + 0.3 * recency + 0.1 * usage
    )


def retrieve(user_id: int, query: str, token_budget: int | None = None) -> list[MemoryEntry]:
    """Pinned entries + the best query matches, within the token budget."""
    settings = get_settings()
    budget = token_budget or settings.memory_token_budget
    now = datetime.now(UTC)

    with read_session() as db:
        pinned = db.exec(
            select(MemoryEntry).where(
                MemoryEntry.user_id == user_id, MemoryEntry.pinned == True  # noqa: E712
            )
        ).all()

    scored = sorted(
        (
            (entry, _rank(entry, match, now))
            for entry, match in _candidates(user_id, query)
            if not entry.pinned
        ),
        key=lambda pair: pair[1],
        reverse=True,
    )

    chosen: list[MemoryEntry] = []
    used = 0
    for entry in [*pinned, *(entry for entry, _ in scored[:RETRIEVE_LIMIT])]:
        cost = estimate_tokens(entry.content) + 6
        if used + cost > budget and chosen:
            continue
        chosen.append(entry)
        used += cost
    return chosen


def render_block(entries: list[MemoryEntry]) -> str:
    if not entries:
        return ""
    lines = [f"- ({entry.kind.value}) {entry.content}" for entry in entries]
    return (
        "Things you remember about this user from previous conversations "
        "(use naturally; never claim you cannot remember):\n" + "\n".join(lines)
    )


def record_use(entries: list[MemoryEntry]) -> None:
    if not entries:
        return
    now = datetime.now(UTC)
    with write_session() as db:
        for entry in entries:
            row = db.get(MemoryEntry, entry.id)
            if row:
                row.use_count += 1
                row.last_used_at = now
                db.add(row)


# ── model access (best-effort — every caller tolerates None) ────────────────

_MODEL_TIMEOUT = None  # created lazily to keep import light


def _timeout():
    import httpx

    return httpx.Timeout(connect=10, read=180, write=30, pool=10)


async def _model_text(prompt: str, system: str, max_tokens: int = 350) -> str | None:
    """One plain completion on the current lease, or None if unavailable."""
    import httpx

    from . import routing
    from .engine_manager import engine_manager

    ready = engine_manager.ready_text_leases()  # imagegen can't answer chat
    if not ready:
        return None
    lease = ready[0]
    base_url = await routing.completion_base_url(lease.base_url)
    body = {
        "model": lease.model_slug,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        "max_tokens": max_tokens,
        "temperature": 0.3,
    }
    try:
        async with httpx.AsyncClient(timeout=_timeout()) as http:
            resp = await http.post(f"{base_url}/chat/completions", json=body)
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"].strip()
    except Exception as exc:
        log.debug("memory model call failed: %s", exc)
        return None


async def _model_json(prompt: str, system: str, max_tokens: int = 500) -> object | None:
    content = await _model_text(prompt, system, max_tokens)
    if not content:
        return None
    match = re.search(r"\[.*\]|\{.*\}", content, re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None


# ── extraction ──────────────────────────────────────────────────────────────

_EXTRACT_SYSTEM = """You maintain a long-term memory store about one user.
From the conversation excerpt, extract at most 3 DURABLE memories worth keeping
for months: stable facts about the user, preferences, ongoing projects, or a
notable episode. Ignore chit-chat, one-off questions, and anything transient.
You are shown the closest EXISTING memories — update or delete those instead of
adding near-duplicates. Reply with ONLY a JSON array of operations:
[{"op":"add","kind":"fact|preference|project|episode","content":"...","importance":0.5-1.5}]
[{"op":"update","id":<existing id>,"content":"...","importance":...}]
[{"op":"delete","id":<existing id>}]
Each content <= 2 sentences, written in third person ("The user ..."). Reply []
if nothing is worth remembering — that is the most common correct answer."""


async def extract_from_exchange(
    user_id: int, conversation_id: str, user_text: str, assistant_text: str
) -> int:
    """Distill one exchange into memory ops. Returns ops applied."""
    with read_session() as db:
        user = db.get(User, user_id)
    if user is None or not user.memory_enabled:
        return 0

    related = _candidates(user_id, user_text, limit=6)
    related_block = "\n".join(
        f"  id={entry.id} ({entry.kind.value}): {entry.content}" for entry, _ in related
    ) or "  (none)"
    prompt = (
        f"EXISTING MEMORIES (closest matches):\n{related_block}\n\n"
        f"CONVERSATION EXCERPT:\nUser: {user_text[:2000]}\n"
        f"Assistant: {assistant_text[:1500]}\n\nJSON operations:"
    )
    ops = await _model_json(prompt, _EXTRACT_SYSTEM, max_tokens=500)
    if not isinstance(ops, list):
        return 0

    applied = 0
    valid_ids = {entry.id for entry, _ in related}
    now = datetime.now(UTC)
    for op in ops[:5]:
        if not isinstance(op, dict):
            continue
        action = op.get("op")
        try:
            if action == "add" and op.get("content"):
                kind = op.get("kind", "fact")
                kind = kind if kind in MemoryKind.__members__ else "fact"
                with write_session() as db:
                    count = len(
                        db.exec(
                            select(MemoryEntry.id).where(MemoryEntry.user_id == user_id)
                        ).all()
                    )
                    if count >= MAX_ENTRIES_PER_USER:
                        continue
                    db.add(
                        MemoryEntry(
                            user_id=user_id,
                            kind=MemoryKind(kind),
                            content=str(op["content"])[:MAX_ENTRY_CHARS],
                            importance=float(op.get("importance", 1.0)),
                            source_conversation_id=conversation_id,
                        )
                    )
                applied += 1
            elif action == "update" and op.get("id") in valid_ids and op.get("content"):
                with write_session() as db:
                    row = db.get(MemoryEntry, int(op["id"]))
                    if row and row.user_id == user_id:
                        row.content = str(op["content"])[:MAX_ENTRY_CHARS]
                        if "importance" in op:
                            row.importance = float(op["importance"])
                        row.updated_at = now
                        db.add(row)
                        applied += 1
            elif action == "delete" and op.get("id") in valid_ids:
                with write_session() as db:
                    row = db.get(MemoryEntry, int(op["id"]))
                    if row and row.user_id == user_id and not row.pinned:
                        db.delete(row)
                        applied += 1
        except (ValueError, TypeError, KeyError):
            continue
    if applied:
        log.info("memory: applied %d op(s) for user %d", applied, user_id)
    return applied


# ── conversation compression ────────────────────────────────────────────────

_SUMMARY_SYSTEM = """You compress chat history. Merge the PREVIOUS SUMMARY and
the OLDER TURNS into one updated summary (<= 200 words) that preserves: goals,
decisions, key facts, names/numbers, unresolved questions, and the user's tone
of engagement. Write dense prose, no preamble. Reply with only the summary."""


async def compress_conversation(conversation_id: str) -> bool:
    """Fold turns beyond the keep-tail into the conversation's rolling summary
    once the live context outgrows the budget. Returns True if compressed."""
    settings = get_settings()
    with read_session() as db:
        conversation = db.get(Conversation, conversation_id)
        if conversation is None:
            return False
        messages = sorted(
            db.exec(
                select(ChatMessage).where(
                    ChatMessage.conversation_id == conversation_id,
                    ChatMessage.id > conversation.summarized_until,  # type: ignore[arg-type]
                )
            ).all(),
            key=lambda m: m.id or 0,
        )

    total = sum(m.token_estimate or estimate_tokens(m.content) for m in messages)
    keep = settings.chat_keep_tail_messages
    if total <= settings.chat_context_tokens or len(messages) <= keep:
        return False

    evict = messages[:-keep]
    turns = "\n".join(f"{m.role}: {m.content[:1200]}" for m in evict)
    prompt = (
        f"PREVIOUS SUMMARY:\n{conversation.summary or '(none)'}\n\n"
        f"OLDER TURNS:\n{turns}\n\nUpdated summary:"
    )
    summary = await _model_text(prompt, _SUMMARY_SYSTEM, max_tokens=350)
    if not summary:
        return False

    with write_session() as db:
        row = db.get(Conversation, conversation_id)
        if row:
            row.summary = summary[:4000]
            row.summarized_until = max((m.id or 0) for m in evict)
            db.add(row)
    return True


# ── consolidation & decay (nightly job) ─────────────────────────────────────


async def consolidate_all() -> dict:
    """Decay unused importance and prune the noise floor."""
    now = datetime.now(UTC)
    pruned = decayed = 0
    with read_session() as db:
        entries = db.exec(select(MemoryEntry)).all()
    for entry in entries:
        if entry.pinned:
            continue
        last = (
            entry.last_used_at
            if entry.last_used_at.tzinfo
            else entry.last_used_at.replace(tzinfo=UTC)
        )
        idle_days = (now - last).total_seconds() / 86400
        if idle_days > 14:
            factor = math.pow(0.5, idle_days / DECAY_HALF_LIFE_DAYS)
            new_importance = round(entry.importance * max(factor, 0.5), 3)
            with write_session() as db:
                row = db.get(MemoryEntry, entry.id)
                if row is None:
                    continue
                if new_importance < PRUNE_IMPORTANCE_FLOOR and row.use_count == 0:
                    db.delete(row)
                    pruned += 1
                elif new_importance < row.importance:
                    row.importance = new_importance
                    db.add(row)
                    decayed += 1
    result = {"decayed": decayed, "pruned": pruned}
    log.info("memory consolidation: %s", result)
    return result


def schedule_background(coro) -> None:
    """Fire-and-forget with logging — memory work must never break a chat."""

    async def runner():
        try:
            await coro
        except Exception:
            log.exception("background memory task failed")

    asyncio.get_running_loop().create_task(runner())
