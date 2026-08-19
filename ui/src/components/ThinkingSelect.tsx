// Compact thinking-level picker (Auto | Off | Low | High) used in the three
// composers: session chat, fire-and-forget tasks and the engine scratch chat.
// Renders as a chip button that opens a small upward popover; the chip tints
// accent when a non-default level is active so the state stays visible.

import { useEffect, useId, useState } from "react";
import type { ThinkingLevel } from "../api/types";
import { cx } from "../lib/utils";
import { IconBrain, IconCheck } from "./icons";

export const THINKING_LEVELS: readonly {
  id: ThinkingLevel;
  label: string;
  hint: string;
}[] = [
  { id: "auto", label: "Auto", hint: "Model default" },
  { id: "off", label: "Off", hint: "Answer directly" },
  { id: "low", label: "Low", hint: "Brief reasoning" },
  { id: "high", label: "High", hint: "Deep reasoning" },
];

function isThinkingLevel(value: unknown): value is ThinkingLevel {
  return value === "auto" || value === "off" || value === "low" || value === "high";
}

/** Read a persisted thinking level (localStorage), defaulting to "auto". */
export function loadStoredThinking(storageKey: string): ThinkingLevel {
  try {
    const raw = localStorage.getItem(storageKey);
    if (isThinkingLevel(raw)) return raw;
  } catch {
    // storage unavailable (private mode) — fall through
  }
  return "auto";
}

export function storeThinking(storageKey: string, level: ThinkingLevel): void {
  try {
    localStorage.setItem(storageKey, level);
  } catch {
    // storage unavailable — the level still applies for this page load
  }
}

export function ThinkingSelect({
  value,
  onChange,
  disabled = false,
  align = "right",
  direction = "up",
}: {
  value: ThinkingLevel;
  onChange: (next: ThinkingLevel) => void;
  disabled?: boolean;
  /** Which edge of the trigger the popover hugs. */
  align?: "left" | "right";
  /** "up" for bottom-anchored composers, "down" for top-of-page forms. */
  direction?: "up" | "down";
}) {
  const [open, setOpen] = useState(false);
  const listId = useId();
  const current =
    THINKING_LEVELS.find((l) => l.id === value) ?? THINKING_LEVELS[0];

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open]);

  return (
    <div className="relative shrink-0">
      <button
        type="button"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={open ? listId : undefined}
        aria-label={`Thinking level: ${current.label}`}
        title={`Thinking: ${current.label}`}
        disabled={disabled}
        onClick={() => setOpen((o) => !o)}
        className={cx(
          "inline-flex min-h-11 cursor-pointer items-center gap-1 rounded-xl border px-2.5 text-xs font-semibold transition-colors duration-150 disabled:cursor-not-allowed disabled:opacity-50",
          value === "auto"
            ? "border-edge bg-raised text-muted hover:text-text"
            : "border-accent/45 bg-accent/15 text-accent",
        )}
      >
        <IconBrain size={17} />
        {value !== "auto" && <span>{current.label}</span>}
      </button>

      {open && (
        <>
          <button
            type="button"
            aria-label="Close thinking menu"
            tabIndex={-1}
            onClick={() => setOpen(false)}
            className="fixed inset-0 z-20 cursor-default"
          />
          <div
            id={listId}
            role="listbox"
            aria-label="Thinking level"
            className={cx(
              "absolute z-30 w-48 animate-rise rounded-xl border border-border bg-surface p-1 shadow-xl shadow-black/50",
              direction === "up" ? "bottom-full mb-2" : "top-full mt-2",
              align === "right" ? "right-0" : "left-0",
            )}
          >
            <p className="px-3 pt-1.5 pb-1 text-[10px] font-semibold tracking-wider text-faint uppercase">
              Thinking
            </p>
            {THINKING_LEVELS.map((opt) => (
              <button
                key={opt.id}
                type="button"
                role="option"
                aria-selected={value === opt.id}
                onClick={() => {
                  onChange(opt.id);
                  setOpen(false);
                }}
                className={cx(
                  "flex min-h-11 w-full cursor-pointer items-center gap-2 rounded-lg px-3 text-left",
                  value === opt.id
                    ? "bg-overlay text-text"
                    : "text-muted hover:bg-raised hover:text-text",
                )}
              >
                <span className="min-w-0 flex-1">
                  <span className="block text-sm font-medium">{opt.label}</span>
                  <span className="block text-[11px] text-faint">
                    {opt.hint}
                  </span>
                </span>
                {value === opt.id && (
                  <IconCheck size={14} className="shrink-0 text-accent" />
                )}
              </button>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
