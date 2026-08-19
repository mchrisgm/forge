import {
  isValidElement,
  memo,
  useCallback,
  useRef,
  useState,
  type ReactNode,
} from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { ApiError, errorMessage } from "../api/client";
import type { SandboxRunResult } from "../api/types";
import { cx } from "../lib/utils";
import { useSandbox } from "./chat/sandbox-context";
import {
  IconCheck,
  IconCopy,
  IconDownload,
  IconPlay,
  IconTerminal,
  IconX,
} from "./icons";
import { Spinner } from "./ui";

/** Fence language → the sandbox language it runs as. Only these get a Run
 *  button; everything else is display-only. */
const RUN_LANGUAGES: Record<string, string> = {
  python: "python",
  py: "python",
  javascript: "javascript",
  js: "javascript",
  node: "node",
  bash: "bash",
  sh: "bash",
  shell: "bash",
  zsh: "bash",
};

type RunState =
  | { status: "running" }
  | { status: "done"; result: SandboxRunResult }
  | { status: "error"; message: string; laneDown: boolean };

/** Fence language → file extension for the Save button's filename guess. */
const LANG_EXTENSIONS: Record<string, string> = {
  bash: "sh",
  c: "c",
  "c++": "cpp",
  cpp: "cpp",
  cs: "cs",
  csharp: "cs",
  css: "css",
  diff: "diff",
  dockerfile: "dockerfile",
  go: "go",
  html: "html",
  ini: "ini",
  java: "java",
  javascript: "js",
  js: "js",
  json: "json",
  jsx: "jsx",
  kotlin: "kt",
  lua: "lua",
  markdown: "md",
  md: "md",
  perl: "pl",
  php: "php",
  py: "py",
  python: "py",
  r: "r",
  rb: "rb",
  ruby: "rb",
  rust: "rs",
  sh: "sh",
  shell: "sh",
  sql: "sql",
  svg: "svg",
  swift: "swift",
  toml: "toml",
  ts: "ts",
  tsx: "tsx",
  typescript: "ts",
  xml: "xml",
  yaml: "yml",
  yml: "yml",
  zsh: "sh",
};

/** The fence language from the inner `<code class="language-…">`, if any. */
function fenceLanguage(children: ReactNode): string {
  if (!isValidElement(children)) return "";
  const className = (children.props as { className?: string }).className ?? "";
  return /language-([\w+-]+)/.exec(className)?.[1]?.toLowerCase() ?? "";
}

/** Filename a fenced block downloads as, guessed from its language. */
function downloadName(language: string): string {
  if (language === "dockerfile") return "Dockerfile";
  const ext = LANG_EXTENSIONS[language] ?? language ?? "";
  return `snippet.${ext || "txt"}`;
}

/** Result panel rendered under a code block after a sandbox run. Built from
 *  divs/spans (not <pre>/<code>) so md-body typography rules don't bleed in. */
function RunResult({
  state,
  onClose,
}: {
  state: RunState;
  onClose: () => void;
}) {
  const done = state.status === "done" ? state.result : null;
  return (
    <div className="mt-1.5 overflow-hidden rounded-lg border border-border bg-surface text-xs">
      <div className="flex items-center justify-between gap-2 border-b border-border bg-raised/50 px-3 py-1.5">
        <span className="flex flex-wrap items-center gap-1.5 font-medium text-muted">
          <IconTerminal size={13} className="shrink-0" />
          Sandbox
          {done && (
            <>
              <span
                className={cx(
                  "font-mono",
                  done.exit_code === 0 ? "text-ok" : "text-danger",
                )}
              >
                exit {done.exit_code}
              </span>
              {done.timed_out && (
                <span className="rounded bg-warn/15 px-1 text-warn">
                  timed out
                </span>
              )}
              <span className="text-faint">· {done.duration_ms} ms</span>
            </>
          )}
          {state.status === "running" && (
            <span className="text-faint">running…</span>
          )}
        </span>
        {state.status !== "running" && (
          <button
            type="button"
            aria-label="Dismiss result"
            onClick={onClose}
            className="-m-1 shrink-0 cursor-pointer p-1 text-faint hover:text-text"
          >
            <IconX size={13} />
          </button>
        )}
      </div>
      <div className="max-h-72 overflow-auto p-3">
        {state.status === "running" && (
          <p className="flex items-center gap-2 text-muted">
            <Spinner size={13} />
            Executing in a microVM…
          </p>
        )}
        {state.status === "error" && (
          <div>
            <p className="break-words text-danger">{state.message}</p>
            {state.laneDown && (
              <p className="mt-1 text-faint">
                Start the sandbox lane with{" "}
                <span className="rounded bg-overlay px-1 font-mono">
                  make sandbox
                </span>
                .
              </p>
            )}
          </div>
        )}
        {done && (
          <div className="space-y-2">
            {done.stdout && (
              <div className="font-mono break-words whitespace-pre-wrap text-text">
                {done.stdout}
              </div>
            )}
            {done.stderr && (
              <div>
                <p className="mb-0.5 font-medium text-danger/80">stderr</p>
                <div className="font-mono break-words whitespace-pre-wrap text-danger">
                  {done.stderr}
                </div>
              </div>
            )}
            {!done.stdout && !done.stderr && (
              <p className="text-faint">No output.</p>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

function CodeBlock({ children }: { children: ReactNode }) {
  const preRef = useRef<HTMLPreElement>(null);
  const [copied, setCopied] = useState(false);
  const [saved, setSaved] = useState(false);
  const [runState, setRunState] = useState<RunState | null>(null);
  const language = fenceLanguage(children);
  const filename = downloadName(language);

  const sandbox = useSandbox();
  const runLang = RUN_LANGUAGES[language];
  const canRun = sandbox != null && runLang != null;
  const running = runState?.status === "running";

  const run = useCallback(() => {
    if (!sandbox || !runLang) return;
    const text = preRef.current?.innerText ?? "";
    if (!text.trim()) return;
    setRunState({ status: "running" });
    sandbox.run(runLang, text).then(
      (result) => setRunState({ status: "done", result }),
      (err: unknown) =>
        setRunState({
          status: "error",
          message: errorMessage(err),
          laneDown: err instanceof ApiError && err.status === 503,
        }),
    );
  }, [sandbox, runLang]);

  const copy = useCallback(() => {
    const text = preRef.current?.innerText ?? "";
    void navigator.clipboard
      ?.writeText(text)
      .then(() => {
        setCopied(true);
        window.setTimeout(() => setCopied(false), 1500);
      })
      .catch(() => {
        /* clipboard unavailable */
      });
  }, []);

  const save = useCallback(() => {
    const text = preRef.current?.innerText ?? "";
    if (!text) return;
    const url = URL.createObjectURL(
      new Blob([text], { type: "text/plain;charset=utf-8" }),
    );
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = filename;
    anchor.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    setSaved(true);
    window.setTimeout(() => setSaved(false), 1500);
  }, [filename]);

  const buttonClass =
    "cursor-pointer rounded-md border border-border bg-raised/90 p-1.5 text-muted hover:text-text";

  return (
    <div className="group relative">
      <pre ref={preRef}>{children}</pre>
      <div className="absolute top-1.5 right-1.5 flex gap-1 opacity-70 transition-opacity group-hover:opacity-100">
        {canRun && (
          <button
            type="button"
            aria-label={running ? "Running" : "Run code in sandbox"}
            title="Run in sandbox"
            onClick={run}
            disabled={running}
            className={cx(buttonClass, "disabled:opacity-60")}
          >
            {running ? (
              <Spinner size={14} />
            ) : (
              <IconPlay size={14} className="text-ok" />
            )}
          </button>
        )}
        <button
          type="button"
          aria-label={saved ? "Saved" : `Save as ${filename}`}
          title={`Save as ${filename}`}
          onClick={save}
          className={buttonClass}
        >
          {saved ? (
            <IconCheck size={14} className="text-ok" />
          ) : (
            <IconDownload size={14} />
          )}
        </button>
        <button
          type="button"
          aria-label={copied ? "Copied" : "Copy code"}
          title="Copy code"
          onClick={copy}
          className={buttonClass}
        >
          {copied ? (
            <IconCheck size={14} className="text-ok" />
          ) : (
            <IconCopy size={14} />
          )}
        </button>
      </div>
      {runState && (
        <RunResult state={runState} onClose={() => setRunState(null)} />
      )}
    </div>
  );
}

const Markdown = memo(function Markdown({ text }: { text: string }) {
  return (
    <div className="md-body text-sm">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          pre: ({ children }) => <CodeBlock>{children}</CodeBlock>,
          a: ({ children, href }) => (
            <a href={href} target="_blank" rel="noreferrer noopener">
              {children}
            </a>
          ),
        }}
      >
        {text}
      </ReactMarkdown>
    </div>
  );
});

export default Markdown;
