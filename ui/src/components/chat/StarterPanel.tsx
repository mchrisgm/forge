// Friendly empty state for a chat with no messages yet: a short heading,
// clickable starter cards that PREFILL the composer (never auto-send), and a
// compact one-line tour of the composer's tools. ChatView hides it the moment
// any message exists — including mid-first-generation and during reattach.

import type { ReactNode } from "react";
import {
  IconBrain,
  IconGhost,
  IconGlobe,
  IconImage,
  IconPaperclip,
  IconTerminal,
} from "../icons";

interface Starter {
  icon: ReactNode;
  title: string;
  hint: string;
  /** Text dropped into the composer when the card is tapped. */
  prompt: string;
}

const STARTERS: Starter[] = [
  {
    icon: <IconGlobe size={16} />,
    title: "Summarize a web page",
    hint: "Or fetch one with the globe button",
    prompt: "Summarize this page: <paste URL>",
  },
  {
    icon: <IconImage size={16} />,
    title: "Generate an image",
    hint: "/imagine works from any chat",
    prompt: "/imagine a cozy reading nook at golden hour",
  },
  {
    icon: <IconPaperclip size={16} />,
    title: "Ask about a file",
    hint: "Attach it with the paperclip first",
    prompt: "What are the key takeaways from the attached file?",
  },
  {
    icon: <IconTerminal size={16} />,
    title: "Write some code",
    hint: "Snippets can run in the sandbox",
    prompt:
      "Write a Python script that renames photos by their EXIF date, with a dry-run flag.",
  },
  {
    icon: <IconBrain size={16} />,
    title: "Reason it out",
    hint: "Thinking levels sit next to send",
    prompt:
      "Think step by step: how many 3 m planks do I need for three raised beds of 1.2 m × 2.4 m × 0.4 m?",
  },
];

/** Inline icon for the tips line — sized to sit in running text. */
function TipIcon({ children }: { children: ReactNode }) {
  return (
    <span aria-hidden className="inline-flex translate-y-[2px]">
      {children}
    </span>
  );
}

export function StarterPanel({
  onPick,
  /** New chats can flip incognito via the ghost toggle — mention it. */
  showTemporaryTip,
  /** The "Auto" model option is available — mention it in the tips line. */
  autoAvailable,
}: {
  onPick: (prompt: string) => void;
  showTemporaryTip: boolean;
  autoAvailable: boolean;
}) {
  return (
    <div className="mx-auto w-full max-w-2xl py-6">
      <h2 className="text-center text-base font-semibold text-text">
        What shall we dig into?
      </h2>
      <p className="mt-1 text-center text-sm text-muted">
        Tap a starter to prefill the composer, or just write your own.
      </p>

      <div className="mt-5 grid gap-2 sm:grid-cols-2">
        {STARTERS.map((s) => (
          <button
            key={s.title}
            type="button"
            onClick={() => onPick(s.prompt)}
            className="group flex cursor-pointer items-start gap-3 rounded-xl border border-border bg-surface p-3.5 text-left transition-colors duration-150 hover:border-accent/40 hover:bg-raised"
          >
            <span className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-raised text-muted transition-colors duration-150 group-hover:text-accent">
              {s.icon}
            </span>
            <span className="min-w-0">
              <span className="block text-sm font-medium text-text">
                {s.title}
              </span>
              <span className="mt-0.5 block text-xs text-faint">{s.hint}</span>
            </span>
          </button>
        ))}
      </div>

      <p className="mt-4 text-center text-xs leading-relaxed text-faint">
        Tools: <TipIcon><IconGlobe size={12} /></TipIcon> reads a page ·{" "}
        <TipIcon><IconPaperclip size={12} /></TipIcon> attaches files ·{" "}
        <span className="font-mono">/imagine</span> generates images · model
        picker above{autoAvailable && " (incl. Auto)"}
        {showTemporaryTip && (
          <>
            {" "}· <TipIcon><IconGhost size={12} /></TipIcon> starts a temporary
            chat
          </>
        )}
      </p>
    </div>
  );
}
