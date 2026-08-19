# Forge user memory

Every profile has its own long-term memory: what you tell Forge in one chat is
available in the next, without replaying whole histories through the model.
The design borrows the best-tested ideas from current memory research
(generative agents' importance/recency scoring, MemGPT-style paging,
reflection-based consolidation) and adapts them to what a single GPU and a
small local model can actually sustain.

## The pipeline

```
      chat exchange (saved conversations only)
            │
            ▼
 ┌─ EXTRACTION ─────────────────────────────────────────────┐
 │ The serving model sees the exchange + the closest         │
 │ EXISTING memories and emits JSON ops: add / update /      │
 │ delete. Showing it what is already known makes memories   │
 │ converge (update-in-place) instead of piling up           │
 │ near-duplicates. "[]" is the most common correct answer.  │
 └───────────────────────────────────────────────────────────┘
            │ MemoryEntry rows: kind ∈ fact | preference |
            ▼ project | episode, importance, provenance
 ┌─ RETRIEVAL (per message) ────────────────────────────────┐
 │ SQLite FTS5 BM25 over content (porter stemming), re-      │
 │ ranked by importance × recency × usage. Pinned entries    │
 │ always injected. Degrades to keyword LIKE without FTS5.   │
 └───────────────────────────────────────────────────────────┘
            │ top-k within FORGE_MEMORY_TOKEN_BUDGET (~700 tok)
            ▼
 ┌─ INJECTION ──────────────────────────────────────────────┐
 │ One compact system block ("Things you remember about      │
 │ this user…"). Injected entries record usage, feeding      │
 │ future ranking — memory that helps gets remembered        │
 │ harder.                                                   │
 └───────────────────────────────────────────────────────────┘
```

## Conversation compression

Long conversations never replay in full. When a conversation's live context
exceeds `FORGE_CHAT_CONTEXT_TOKENS` (default ~6000), the oldest turns beyond a
keep-tail (default 12 messages) are folded into a **rolling summary**:

```
new_summary = model(previous_summary + evicted_turns)
```

The summary is incremental (each compression merges into the last one, like a
log-structured compaction), capped at ~200 words, and stored on the
conversation row with a `summarized_until` watermark. Continuing a month-old
chat costs: system prompt + memory block + summary + recent tail — a few
thousand tokens regardless of how long the history is, with names, decisions,
and open questions preserved.

## Consolidation & decay

A nightly job keeps the store healthy:

- **Decay** — importance decays with disuse (half-life 90 days, floored at
  50% per pass), so stale trivia sinks in ranking without vanishing abruptly.
- **Prune** — never-used entries that decay below the noise floor are removed.
- **Convergence-by-extraction** — because every extraction sees its nearest
  neighbors, dedup happens continuously at write time, not just nightly.

Pinned memories are exempt from decay and pruning and always injected.

## Token economics

| Piece | Cost per message |
|---|---|
| Memory block | ≤ ~700 tokens (budgeted, usually far less) |
| Rolling summary | ≤ ~270 tokens |
| History tail | greedy newest-first fit into the remaining budget |
| Extraction (background) | one small completion per exchange, off the hot path |

Everything model-driven (extraction, summarization, titling) runs in the
background on whatever GPU lease is serving and silently skips when no engine
is loaded — memory never blocks or breaks a chat.

## User control

The Memory page shows every entry with kind, importance, and provenance.
You can edit, pin, delete, add your own (they start above extracted ones in
importance), or wipe the store entirely. Memory can be switched off per
profile, per conversation — and **temporary chats never read or write memory
at all**, and are never stored.

## Honest limitations

- Retrieval is lexical (BM25), not semantic — a deliberate choice: it needs no
  embedding model resident next to a 30B coder on 12 GB of VRAM, it's fast,
  and porter stemming + the model's own query phrasing covers most recall on
  personal-scale stores (≤400 entries/user). Plugging an embedding model into
  the llama.cpp `--embeddings` flag is the documented upgrade path.
- Extraction quality tracks the loaded model; a 7B utility model extracts
  noisier memories than a 30B. Ops are capped (3/exchange) and content is
  length-limited to bound the damage of a bad extraction.
