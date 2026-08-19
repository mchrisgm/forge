// Bridges the chat surface's sandbox-lane health down to the code blocks that
// react-markdown renders (a lazy chunk), so a "Run" button appears on runnable
// snippets only when the smolvm lane is actually reachable.

import { createContext, useContext } from "react";
import type { SandboxRunResult } from "../../api/types";

export interface SandboxRunner {
  /** Run one snippet in the sandbox; rejects with ApiError on failure. */
  run: (language: string, code: string) => Promise<SandboxRunResult>;
}

/** Non-null only while the lane is healthy — the Run button keys off this. */
export const SandboxContext = createContext<SandboxRunner | null>(null);

export function useSandbox(): SandboxRunner | null {
  return useContext(SandboxContext);
}
