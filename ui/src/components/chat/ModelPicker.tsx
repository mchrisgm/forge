// Searchable model dropdown for the chat surface. A compact trigger opens a
// downward popover (mirrors ThinkingSelect's chip + absolute-popover pattern,
// but opening DOWN and with a search field): an "Auto" router row plus one row
// per downloaded model. The backend loads the chosen model on demand, so every
// downloaded model is selectable here — not just the ones already serving; a
// small "loaded" dot marks the models a lease is already holding.

import { useEffect, useId, useMemo, useRef, useState } from "react";
import type { EngineKind } from "../../api/types";
import { cx } from "../../lib/utils";
import { IconChevronDown, IconCheck, IconSearch, IconSparkles } from "../icons";
import { LaneBadge } from "../ui";

/** The sentinel slug for the router-picked "Auto" model (backend AUTO_SLUG). */
const AUTO_SLUG = "auto";

export interface ModelOption {
  slug: string;
  name: string;
  paramsB: number;
  engine: EngineKind;
  /** A lease is already holding this model (shown as a small "loaded" dot). */
  loaded: boolean;
}

/** Compact "7B" / "14B" / "0.6B" / "30.5B" — integer when whole, else 1dp. */
function formatParams(paramsB: number): string {
  if (!Number.isFinite(paramsB) || paramsB <= 0) return "";
  if (paramsB < 1) return `${paramsB.toFixed(1)}B`;
  return Number.isInteger(paramsB) ? `${paramsB}B` : `${paramsB.toFixed(1)}B`;
}

/** One selectable entry: the virtual Auto row, or a concrete model. */
type Row = { kind: "auto" } | { kind: "model"; option: ModelOption };

export function ModelPicker({
  value,
  options,
  autoAvailable,
  disabled = false,
  onChange,
}: {
  /** Current slug — "" or "auto" both mean the Auto router option. */
  value: string;
  options: ModelOption[];
  autoAvailable: boolean;
  disabled?: boolean;
  onChange: (slug: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [highlight, setHighlight] = useState(0);
  const listId = useId();
  const triggerRef = useRef<HTMLButtonElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  const rowRefs = useRef<(HTMLButtonElement | null)[]>([]);

  // Return focus to the trigger when closing via keyboard, so a keyboard-only
  // user isn't stranded with focus on a detached menu element.
  const closeToTrigger = () => {
    setOpen(false);
    triggerRef.current?.focus();
  };

  const isAuto = value === AUTO_SLUG || value === "";

  // Biggest model first — deterministic (params desc, then name).
  const sorted = useMemo(
    () =>
      [...options].sort(
        (a, b) => b.paramsB - a.paramsB || a.name.localeCompare(b.name),
      ),
    [options],
  );

  const selected = isAuto ? null : sorted.find((o) => o.slug === value) ?? null;

  // Rows visible for the current query: Auto (when usable and matching), then
  // models whose name or slug contains the query.
  const rows = useMemo<Row[]>(() => {
    const q = query.trim().toLowerCase();
    const out: Row[] = [];
    if (autoAvailable && (q === "" || "auto".includes(q))) {
      out.push({ kind: "auto" });
    }
    for (const option of sorted) {
      if (
        q === "" ||
        option.name.toLowerCase().includes(q) ||
        option.slug.toLowerCase().includes(q)
      ) {
        out.push({ kind: "model", option });
      }
    }
    return out;
  }, [sorted, autoAvailable, query]);

  const rowSlug = (row: Row) => (row.kind === "auto" ? AUTO_SLUG : row.option.slug);
  const isSelectedRow = (row: Row) =>
    row.kind === "auto" ? isAuto : row.option.slug === value;

  // Clear the search whenever the menu closes so it reopens on a clean list.
  useEffect(() => {
    if (!open) setQuery("");
  }, [open]);

  // On open, focus the search and land the highlight on the current pick.
  // Computed against the empty-query layout ([Auto?, ...sorted]) so it is
  // correct regardless of any residual query state.
  useEffect(() => {
    if (!open) return;
    const base = autoAvailable ? 1 : 0;
    let idx = autoAvailable && isAuto ? 0 : -1;
    if (idx < 0) {
      const m = sorted.findIndex((o) => o.slug === value);
      idx = m >= 0 ? base + m : 0;
    }
    setHighlight(idx);
    const raf = requestAnimationFrame(() => searchRef.current?.focus());
    return () => cancelAnimationFrame(raf);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  // Keep the highlight in range as the filtered list shrinks/grows.
  useEffect(() => {
    setHighlight((h) => Math.min(h, Math.max(0, rows.length - 1)));
  }, [rows.length]);

  // Escape closes from anywhere while open, returning focus to the trigger.
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        closeToTrigger();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  // Scroll the highlighted row into view as it moves.
  useEffect(() => {
    if (open) rowRefs.current[highlight]?.scrollIntoView({ block: "nearest" });
  }, [highlight, open]);

  const commit = (slug: string) => {
    onChange(slug);
    setOpen(false);
  };

  const onSearchKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setHighlight((h) => (rows.length ? (h + 1) % rows.length : 0));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setHighlight((h) => (rows.length ? (h - 1 + rows.length) % rows.length : 0));
    } else if (e.key === "Enter") {
      e.preventDefault();
      const row = rows[highlight];
      if (row) {
        commit(rowSlug(row));
        triggerRef.current?.focus(); // keyboard selection returns focus
      }
    }
  };

  const activeId = rows[highlight] ? `${listId}-opt-${highlight}` : undefined;

  return (
    <div className="relative shrink-0">
      <button
        ref={triggerRef}
        type="button"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={open ? listId : undefined}
        aria-label={
          isAuto
            ? "Model: Auto"
            : `Model: ${selected?.name ?? value}`
        }
        disabled={disabled}
        onClick={() => setOpen((o) => !o)}
        className={cx(
          "inline-flex min-h-11 max-w-[14rem] cursor-pointer items-center gap-1.5 rounded-xl border px-2.5 text-xs font-semibold transition-colors duration-150 disabled:cursor-not-allowed disabled:opacity-50 sm:max-w-[18rem]",
          open
            ? "border-accent/45 bg-accent/15 text-accent"
            : "border-edge bg-raised text-text hover:border-edge hover:bg-overlay",
        )}
      >
        {isAuto ? (
          <>
            <IconSparkles size={16} className="shrink-0 text-accent" />
            <span className="font-sans">Auto</span>
          </>
        ) : selected ? (
          <>
            <span className="truncate font-mono">{selected.name}</span>
            <LaneBadge engine={selected.engine} />
          </>
        ) : (
          <span className="truncate font-mono">{value}</span>
        )}
        <IconChevronDown
          size={15}
          className={cx(
            "shrink-0 text-faint transition-transform duration-150",
            open && "rotate-180",
          )}
        />
      </button>

      {open && (
        <>
          <button
            type="button"
            aria-label="Close model menu"
            tabIndex={-1}
            onClick={() => setOpen(false)}
            className="fixed inset-0 z-20 cursor-default"
          />
          <div
            className="absolute top-full right-0 z-30 mt-2 w-80 max-w-[calc(100vw-2rem)] origin-top animate-pop overflow-hidden rounded-xl border border-border bg-surface shadow-xl shadow-black/50"
          >
            {/* Search */}
            <div className="border-b border-border p-2">
              <div className="relative">
                <IconSearch
                  size={15}
                  className="pointer-events-none absolute top-1/2 left-2.5 -translate-y-1/2 text-faint"
                />
                <input
                  ref={searchRef}
                  type="text"
                  role="combobox"
                  aria-expanded={open}
                  aria-controls={listId}
                  aria-autocomplete="list"
                  aria-activedescendant={activeId}
                  aria-label="Search models"
                  placeholder="Search models…"
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  onKeyDown={onSearchKeyDown}
                  className="min-h-9 w-full rounded-lg border border-edge bg-raised pl-8 pr-2.5 text-sm text-text placeholder:text-faint focus:border-accent focus:outline-none"
                />
              </div>
            </div>

            {/* Options */}
            <div
              id={listId}
              role="listbox"
              aria-label="Model for this chat"
              className="max-h-72 overflow-y-auto p-1"
            >
              {rows.length === 0 && (
                <p className="px-3 py-6 text-center text-sm text-faint">
                  No models match
                </p>
              )}
              {rows.map((row, idx) => {
                const selectedRow = isSelectedRow(row);
                const highlighted = idx === highlight;
                const params =
                  row.kind === "model" ? formatParams(row.option.paramsB) : "";
                return (
                  <button
                    key={rowSlug(row)}
                    id={`${listId}-opt-${idx}`}
                    ref={(el) => {
                      rowRefs.current[idx] = el;
                    }}
                    type="button"
                    role="option"
                    aria-selected={selectedRow}
                    onClick={() => commit(rowSlug(row))}
                    onMouseMove={() => setHighlight(idx)}
                    className={cx(
                      "flex min-h-11 w-full cursor-pointer items-center gap-2 rounded-lg px-3 text-left transition-colors duration-150",
                      selectedRow
                        ? "bg-accent/15 text-accent"
                        : highlighted
                          ? "bg-raised text-text"
                          : "text-muted hover:bg-raised hover:text-text",
                      highlighted && !selectedRow && "ring-1 ring-inset ring-edge",
                    )}
                  >
                    {row.kind === "auto" ? (
                      <>
                        <IconSparkles
                          size={16}
                          className={cx(
                            "shrink-0",
                            selectedRow ? "text-accent" : "text-accent/80",
                          )}
                        />
                        <span className="min-w-0 flex-1">
                          <span className="block font-sans text-sm font-semibold">
                            Auto
                          </span>
                          <span className="block font-sans text-[11px] text-faint">
                            Picks the best model per prompt
                          </span>
                        </span>
                      </>
                    ) : (
                      <>
                        <span className="min-w-0 flex-1 truncate font-mono text-sm">
                          {row.option.name}
                        </span>
                        {params && (
                          <span className="shrink-0 rounded bg-overlay px-1.5 py-0.5 font-mono text-[11px] text-muted">
                            {params}
                          </span>
                        )}
                        <LaneBadge engine={row.option.engine} />
                        {row.option.loaded && (
                          <span
                            title="loaded"
                            aria-label="loaded"
                            className="h-1.5 w-1.5 shrink-0 rounded-full bg-ok"
                          />
                        )}
                      </>
                    )}
                    {selectedRow && (
                      <IconCheck size={15} className="shrink-0 text-accent" />
                    )}
                  </button>
                );
              })}
            </div>
          </div>
        </>
      )}
    </div>
  );
}
