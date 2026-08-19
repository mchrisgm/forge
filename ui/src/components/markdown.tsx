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
import { IconCheck, IconCopy, IconDownload } from "./icons";

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

function CodeBlock({ children }: { children: ReactNode }) {
  const preRef = useRef<HTMLPreElement>(null);
  const [copied, setCopied] = useState(false);
  const [saved, setSaved] = useState(false);
  const filename = downloadName(fenceLanguage(children));

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
