"""Unit tests for app/services/memory.py: token estimation, FTS/LIKE
retrieval with budgeting, the rendered injection block, usage recording,
extraction ops (with the model call mocked), rolling conversation
compression, and the nightly decay/prune consolidation."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlmodel import select

from app import config
from app import db as db_module
from app.models import ChatMessage, Conversation, MemoryEntry, MemoryKind, User
from app.services import memory

pytestmark = pytest.mark.usefixtures("db_ready")


def make_user(username: str = "mem-user", memory_enabled: bool = True) -> int:
    with db_module.write_session() as db:
        user = User(username=username, memory_enabled=memory_enabled)
        db.add(user)
        db.flush()
        return user.id


def add_entry(
    user_id: int,
    content: str,
    kind: MemoryKind = MemoryKind.fact,
    pinned: bool = False,
    importance: float = 1.0,
    use_count: int = 0,
    days_idle: float = 0.0,
) -> int:
    stamp = datetime.now(UTC) - timedelta(days=days_idle)
    with db_module.write_session() as db:
        entry = MemoryEntry(
            user_id=user_id,
            content=content,
            kind=kind,
            pinned=pinned,
            importance=importance,
            use_count=use_count,
            last_used_at=stamp,
        )
        db.add(entry)
        db.flush()
        return entry.id


def get_entry(entry_id: int) -> MemoryEntry | None:
    with db_module.read_session() as db:
        return db.get(MemoryEntry, entry_id)


def entry_ids(user_id: int) -> set[int]:
    with db_module.read_session() as db:
        rows = db.exec(
            select(MemoryEntry.id).where(MemoryEntry.user_id == user_id)
        ).all()
    return set(rows)


def make_conversation(user_id: int) -> str:
    with db_module.write_session() as db:
        conversation = Conversation(user_id=user_id)
        db.add(conversation)
        db.flush()
        return conversation.id


def add_message(conversation_id: str, role: str, content: str, tokens: int) -> int:
    with db_module.write_session() as db:
        message = ChatMessage(
            conversation_id=conversation_id,
            role=role,
            content=content,
            token_estimate=tokens,
        )
        db.add(message)
        db.flush()
        return message.id


@pytest.fixture
def model_json_stub(monkeypatch):
    """memory._model_json returns a canned value and records its prompts."""
    state = {"ops": None, "prompts": []}

    async def fake(prompt, system, max_tokens=500):
        state["prompts"].append(prompt)
        return state["ops"]

    monkeypatch.setattr(memory, "_model_json", fake)
    return state


@pytest.fixture
def model_text_stub(monkeypatch):
    state = {"reply": "compact summary text", "prompts": []}

    async def fake(prompt, system, max_tokens=350):
        state["prompts"].append(prompt)
        return state["reply"]

    monkeypatch.setattr(memory, "_model_text", fake)
    return state


# ── token estimation & term extraction ──────────────────────────────────────


class TestEstimateTokens:
    def test_four_chars_per_token(self):
        assert memory.estimate_tokens("a" * 8) == 2
        assert memory.estimate_tokens("a" * 400) == 100

    def test_never_below_one(self):
        assert memory.estimate_tokens("") == 1
        assert memory.estimate_tokens("ab") == 1


class TestFtsTerms:
    def test_lowercases_dedupes_and_joins_with_or(self):
        assert memory._fts_terms("Rust rust RUST memory") == "rust OR memory"

    def test_short_words_are_dropped(self):
        assert memory._fts_terms("go is ok") == ""
        assert memory._fts_terms("we use python") == "use OR python"

    def test_punctuation_splits_terms(self):
        assert memory._fts_terms("python3.11, fastapi!") == "python3 OR fastapi"

    def test_at_most_twelve_terms(self):
        query = " ".join(f"word{i:02d}" for i in range(20))
        terms = memory._fts_terms(query)
        assert len(terms.split(" OR ")) == 12


# ── retrieval ───────────────────────────────────────────────────────────────


class TestRetrieveFts:
    def test_fts_is_available_in_this_environment(self):
        assert db_module.fts_available() is True

    def test_returns_matching_entries_for_the_right_user_only(self):
        user_id = make_user()
        other_id = make_user("someone-else")
        match_id = add_entry(user_id, "The user is learning woodworking joinery")
        add_entry(user_id, "The user dislikes cilantro in every form")
        add_entry(other_id, "The user is learning woodworking joinery")

        found = memory.retrieve(user_id, "how do I improve my woodworking?")
        assert [entry.id for entry in found] == [match_id]
        assert all(entry.user_id == user_id for entry in found)

    def test_pinned_entries_are_always_included(self):
        user_id = make_user()
        pinned_id = add_entry(user_id, "Call the user Captain", pinned=True)
        match_id = add_entry(user_id, "The user maintains a fastapi service")

        found = memory.retrieve(user_id, "debugging my fastapi service")
        ids = [entry.id for entry in found]
        assert pinned_id in ids and match_id in ids
        # Pinned first, even though it matches nothing in the query.
        assert ids[0] == pinned_id

    def test_token_budget_is_respected(self):
        user_id = make_user()
        first = add_entry(user_id, "x" * 40, pinned=True)  # 10 + 6 tokens
        second = add_entry(user_id, "y" * 40, pinned=True)
        found = memory.retrieve(user_id, "anything at all", token_budget=20)
        assert [entry.id for entry in found] == [first]
        # A budget wide enough takes both.
        found = memory.retrieve(user_id, "anything at all", token_budget=40)
        assert {entry.id for entry in found} == {first, second}

    def test_unmatchable_query_still_returns_pinned(self):
        user_id = make_user()
        pinned_id = add_entry(user_id, "The user is left handed", pinned=True)
        add_entry(user_id, "The user plays chess on Tuesdays")
        found = memory.retrieve(user_id, "!!! ??")
        assert [entry.id for entry in found] == [pinned_id]


class TestRetrieveLikeFallback:
    @pytest.fixture(autouse=True)
    def no_fts(self, monkeypatch):
        monkeypatch.setattr(memory, "fts_available", lambda: False)

    def test_like_scan_matches_keywords(self):
        user_id = make_user()
        match_id = add_entry(user_id, "The user grows heirloom tomatoes")
        add_entry(user_id, "The user prefers dark mode")
        found = memory.retrieve(user_id, "advice about tomatoes please")
        assert [entry.id for entry in found] == [match_id]

    def test_pinned_still_included_without_fts(self):
        user_id = make_user()
        pinned_id = add_entry(user_id, "Speak formally", pinned=True)
        found = memory.retrieve(user_id, "anything")
        assert [entry.id for entry in found] == [pinned_id]

    def test_other_users_entries_never_leak(self):
        user_id = make_user()
        other_id = make_user("stranger")
        add_entry(other_id, "The user grows heirloom tomatoes")
        assert memory.retrieve(user_id, "tomatoes advice") == []


class TestRenderBlock:
    def test_empty_is_empty_string(self):
        assert memory.render_block([]) == ""

    def test_lines_carry_kind_and_content(self):
        entries = [
            MemoryEntry(user_id=1, kind=MemoryKind.fact, content="Has two cats"),
            MemoryEntry(
                user_id=1, kind=MemoryKind.preference, content="Prefers brevity"
            ),
        ]
        block = memory.render_block(entries)
        assert "Things you remember about this user" in block
        assert "- (fact) Has two cats" in block
        assert "- (preference) Prefers brevity" in block


class TestRecordUse:
    def test_increments_use_count_and_touches_last_used(self):
        user_id = make_user()
        entry_id = add_entry(user_id, "The user works night shifts", days_idle=30)
        before = get_entry(entry_id)
        memory.record_use([before])
        after = get_entry(entry_id)
        assert after.use_count == 1
        assert after.last_used_at > before.last_used_at

    def test_empty_list_is_a_noop(self):
        memory.record_use([])  # must not raise or open a write txn


# ── extraction ──────────────────────────────────────────────────────────────


class TestExtractFromExchange:
    async def test_add_op_creates_an_entry(self, model_json_stub):
        user_id = make_user()
        model_json_stub["ops"] = [
            {
                "op": "add",
                "kind": "preference",
                "content": "The user prefers tabs over spaces",
                "importance": 1.2,
            }
        ]
        applied = await memory.extract_from_exchange(
            user_id, "conv-1", "tabs or spaces?", "tabs it is"
        )
        assert applied == 1
        with db_module.read_session() as db:
            (entry,) = db.exec(
                select(MemoryEntry).where(MemoryEntry.user_id == user_id)
            ).all()
        assert entry.kind == MemoryKind.preference
        assert entry.content == "The user prefers tabs over spaces"
        assert entry.importance == 1.2
        assert entry.source_conversation_id == "conv-1"

    async def test_unknown_kind_falls_back_to_fact_and_content_is_capped(
        self, model_json_stub
    ):
        user_id = make_user()
        model_json_stub["ops"] = [
            {"op": "add", "kind": "banana", "content": "z" * 900}
        ]
        assert await memory.extract_from_exchange(user_id, "c", "u", "a") == 1
        with db_module.read_session() as db:
            (entry,) = db.exec(
                select(MemoryEntry).where(MemoryEntry.user_id == user_id)
            ).all()
        assert entry.kind == MemoryKind.fact
        assert len(entry.content) == memory.MAX_ENTRY_CHARS

    async def test_update_and_delete_apply_to_related_entries(self, model_json_stub):
        user_id = make_user()
        keep_id = add_entry(user_id, "The user is learning woodworking joinery")
        drop_id = add_entry(user_id, "The user started a woodworking bench project")
        model_json_stub["ops"] = [
            {"op": "update", "id": keep_id, "content": "Now a woodworking expert",
             "importance": 1.5},
            {"op": "delete", "id": drop_id},
        ]
        applied = await memory.extract_from_exchange(
            user_id, "c", "my woodworking bench and joinery progress", "nice"
        )
        assert applied == 2
        assert get_entry(drop_id) is None
        updated = get_entry(keep_id)
        assert updated.content == "Now a woodworking expert"
        assert updated.importance == 1.5

    async def test_ops_against_unrelated_ids_are_ignored(self, model_json_stub):
        user_id = make_user()
        entry_id = add_entry(user_id, "The user is learning woodworking joinery")
        model_json_stub["ops"] = [
            {"op": "update", "id": 99999, "content": "hijacked"},
            {"op": "delete", "id": 99999},
        ]
        applied = await memory.extract_from_exchange(
            user_id, "c", "woodworking joinery talk", "ok"
        )
        assert applied == 0
        assert get_entry(entry_id).content == "The user is learning woodworking joinery"

    async def test_pinned_entries_cannot_be_deleted_by_ops(self, model_json_stub):
        user_id = make_user()
        pinned_id = add_entry(
            user_id, "The user is learning woodworking joinery", pinned=True
        )
        model_json_stub["ops"] = [{"op": "delete", "id": pinned_id}]
        applied = await memory.extract_from_exchange(
            user_id, "c", "woodworking joinery talk", "ok"
        )
        assert applied == 0
        assert get_entry(pinned_id) is not None

    async def test_add_respects_the_per_user_cap(self, model_json_stub, monkeypatch):
        monkeypatch.setattr(memory, "MAX_ENTRIES_PER_USER", 1)
        user_id = make_user()
        add_entry(user_id, "The user already has one memory")
        model_json_stub["ops"] = [{"op": "add", "content": "one too many"}]
        assert await memory.extract_from_exchange(user_id, "c", "u", "a") == 0
        assert len(entry_ids(user_id)) == 1

    async def test_at_most_five_ops_are_applied(self, model_json_stub):
        user_id = make_user()
        model_json_stub["ops"] = [
            {"op": "add", "content": f"memory number {i}"} for i in range(7)
        ]
        assert await memory.extract_from_exchange(user_id, "c", "u", "a") == 5
        assert len(entry_ids(user_id)) == 5

    async def test_non_list_reply_applies_nothing(self, model_json_stub):
        user_id = make_user()
        model_json_stub["ops"] = {"op": "add", "content": "not a list"}
        assert await memory.extract_from_exchange(user_id, "c", "u", "a") == 0
        model_json_stub["ops"] = None  # model unavailable
        assert await memory.extract_from_exchange(user_id, "c", "u", "a") == 0
        assert entry_ids(user_id) == set()

    async def test_disabled_user_memory_short_circuits(self, model_json_stub):
        user_id = make_user(memory_enabled=False)
        model_json_stub["ops"] = [{"op": "add", "content": "should never land"}]
        assert await memory.extract_from_exchange(user_id, "c", "u", "a") == 0
        assert entry_ids(user_id) == set()
        assert model_json_stub["prompts"] == []  # the model was never dialed

    async def test_prompt_shows_the_closest_existing_memories(self, model_json_stub):
        user_id = make_user()
        entry_id = add_entry(user_id, "The user is learning woodworking joinery")
        model_json_stub["ops"] = []
        await memory.extract_from_exchange(
            user_id, "c", "woodworking joinery update", "cool"
        )
        (prompt,) = model_json_stub["prompts"]
        assert f"id={entry_id}" in prompt
        assert "woodworking joinery update" in prompt


# ── conversation compression ────────────────────────────────────────────────


class TestCompressConversation:
    def _stuffed_conversation(self, extra_messages: int = 4) -> tuple[str, list[int]]:
        """A conversation whose live context is far over budget: keep-tail + a
        few evictable turns, each alone bigger than the whole budget."""
        settings = config.get_settings()
        user_id = make_user()
        conversation_id = make_conversation(user_id)
        count = settings.chat_keep_tail_messages + extra_messages
        ids = [
            add_message(
                conversation_id,
                "user" if i % 2 == 0 else "assistant",
                f"turn {i}",
                tokens=settings.chat_context_tokens,
            )
            for i in range(count)
        ]
        return conversation_id, ids

    def _conversation_row(self, conversation_id: str) -> Conversation:
        with db_module.read_session() as db:
            return db.get(Conversation, conversation_id)

    async def test_noop_under_budget(self, model_text_stub):
        user_id = make_user()
        conversation_id = make_conversation(user_id)
        for i in range(3):
            add_message(conversation_id, "user", f"short {i}", tokens=5)
        assert await memory.compress_conversation(conversation_id) is False
        assert model_text_stub["prompts"] == []
        assert self._conversation_row(conversation_id).summary == ""

    async def test_folds_old_turns_and_advances_summarized_until(
        self, model_text_stub
    ):
        conversation_id, ids = self._stuffed_conversation(extra_messages=4)
        assert await memory.compress_conversation(conversation_id) is True

        row = self._conversation_row(conversation_id)
        assert row.summary == "compact summary text"
        assert row.summarized_until == ids[3]  # 4 evicted, keep-tail preserved

        # The evicted turns (and only those) were handed to the summarizer.
        (prompt,) = model_text_stub["prompts"]
        assert "turn 0" in prompt and "turn 3" in prompt
        assert "turn 4" not in prompt

        # Immediately after, the remaining tail fits the keep policy: no-op,
        # and summarized_until does not regress.
        assert await memory.compress_conversation(conversation_id) is False
        assert self._conversation_row(conversation_id).summarized_until == ids[3]

    async def test_incremental_summaries_feed_the_previous_one_back(
        self, model_text_stub
    ):
        conversation_id, ids = self._stuffed_conversation(extra_messages=4)
        await memory.compress_conversation(conversation_id)
        # The conversation keeps growing past the budget again.
        settings = config.get_settings()
        for i in range(4):
            add_message(
                conversation_id, "user", f"late turn {i}",
                tokens=settings.chat_context_tokens,
            )
        assert await memory.compress_conversation(conversation_id) is True
        assert "compact summary text" in model_text_stub["prompts"][1]

    async def test_model_unavailable_leaves_everything_untouched(
        self, model_text_stub
    ):
        model_text_stub["reply"] = None
        conversation_id, _ = self._stuffed_conversation()
        assert await memory.compress_conversation(conversation_id) is False
        row = self._conversation_row(conversation_id)
        assert row.summary == ""
        assert row.summarized_until == 0

    async def test_missing_conversation_is_false(self, model_text_stub):
        assert await memory.compress_conversation("no-such-id") is False


# ── consolidation & decay ───────────────────────────────────────────────────


class TestConsolidateAll:
    async def test_decays_prunes_and_spares_correctly(self):
        user_id = make_user()
        pinned_id = add_entry(
            user_id, "pinned forever", pinned=True, importance=1.0, days_idle=400
        )
        fresh_id = add_entry(user_id, "fresh memory", importance=1.0, days_idle=2)
        decayed_id = add_entry(user_id, "stale but strong", importance=1.0, days_idle=400)
        protected_id = add_entry(
            user_id, "stale but used", importance=0.2, use_count=3, days_idle=400
        )
        pruned_id = add_entry(
            user_id, "stale and unused", importance=0.2, use_count=0, days_idle=400
        )

        result = await memory.consolidate_all()

        assert result == {"decayed": 2, "pruned": 1}
        assert get_entry(pruned_id) is None
        # 400 idle days clamps the decay factor at 0.5 exactly.
        assert get_entry(decayed_id).importance == 0.5
        assert get_entry(protected_id).importance == 0.1  # under floor but used
        assert get_entry(pinned_id).importance == 1.0
        assert get_entry(fresh_id).importance == 1.0

    async def test_empty_store_reports_zeroes(self):
        assert await memory.consolidate_all() == {"decayed": 0, "pruned": 0}
