import {
  useCallback,
  useEffect,
  useId,
  useRef,
  useState,
  type ButtonHTMLAttributes,
  type InputHTMLAttributes,
  type ReactNode,
  type SelectHTMLAttributes,
  type TextareaHTMLAttributes,
} from "react";
import { cx } from "../lib/utils";
import { IconChevronDown, IconChevronRight, IconRefresh, IconX } from "./icons";

// ── Button ──────────────────────────────────────────────────────────────────

type ButtonVariant = "primary" | "secondary" | "ghost" | "danger";

const BUTTON_VARIANTS: Record<ButtonVariant, string> = {
  primary:
    "bg-accent text-on-accent font-semibold hover:bg-accent-hi active:bg-accent-dim disabled:bg-overlay disabled:text-faint",
  secondary:
    "bg-raised text-text border border-edge hover:bg-overlay disabled:text-faint disabled:hover:bg-raised",
  ghost: "text-muted hover:text-text hover:bg-raised disabled:text-faint",
  danger:
    "bg-danger/10 text-danger border border-danger/30 hover:bg-danger/20 disabled:opacity-50",
};

export function Button({
  variant = "secondary",
  size = "md",
  loading = false,
  className,
  children,
  disabled,
  type,
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: ButtonVariant;
  size?: "sm" | "md";
  loading?: boolean;
}) {
  return (
    <button
      type={type ?? "button"}
      disabled={disabled || loading}
      className={cx(
        "inline-flex cursor-pointer items-center justify-center gap-1.5 rounded-md text-sm transition-colors duration-150 disabled:cursor-not-allowed",
        size === "sm" ? "min-h-9 px-2.5" : "min-h-11 px-4",
        BUTTON_VARIANTS[variant],
        className,
      )}
      {...rest}
    >
      {loading && <Spinner size={14} />}
      {children}
    </button>
  );
}

// ── Spinner ─────────────────────────────────────────────────────────────────

export function Spinner({ size = 16 }: { size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      aria-hidden
      className="animate-spin"
    >
      <circle
        cx="12"
        cy="12"
        r="9"
        stroke="currentColor"
        strokeOpacity="0.25"
        strokeWidth="3"
      />
      <path
        d="M21 12a9 9 0 0 0-9-9"
        stroke="currentColor"
        strokeWidth="3"
        strokeLinecap="round"
      />
    </svg>
  );
}

// ── Chips & badges ──────────────────────────────────────────────────────────

export function Chip({
  color,
  pulse = false,
  children,
}: {
  color: string; // tailwind text color class, drives the dot via currentColor
  pulse?: boolean;
  children: ReactNode;
}) {
  return (
    <span
      className={cx(
        "inline-flex items-center gap-1.5 rounded-full border border-border bg-raised px-2 py-0.5 text-xs font-medium",
        color,
      )}
    >
      <span
        aria-hidden
        className={cx(
          "h-1.5 w-1.5 rounded-full bg-current",
          pulse && "animate-pulse-dot",
        )}
      />
      <span className="text-text/90">{children}</span>
    </span>
  );
}

const SESSION_CHIP: Record<string, { color: string; pulse: boolean }> = {
  creating: { color: "text-warn", pulse: true },
  running: { color: "text-ok", pulse: true },
  idle: { color: "text-info", pulse: false },
  stopped: { color: "text-faint", pulse: false },
  error: { color: "text-danger", pulse: false },
};

export function SessionStateChip({ state }: { state: string }) {
  const cfg = SESSION_CHIP[state] ?? { color: "text-faint", pulse: false };
  return (
    <Chip color={cfg.color} pulse={cfg.pulse}>
      {state}
    </Chip>
  );
}

const MODEL_CHIP: Record<string, { color: string; pulse: boolean }> = {
  suggested: { color: "text-faint", pulse: false },
  approved: { color: "text-info", pulse: false },
  downloading: { color: "text-info", pulse: true },
  ready: { color: "text-ok", pulse: false },
  failed: { color: "text-danger", pulse: false },
};

export function ModelStatusChip({ status }: { status: string }) {
  const cfg = MODEL_CHIP[status] ?? { color: "text-faint", pulse: false };
  return (
    <Chip color={cfg.color} pulse={cfg.pulse}>
      {status}
    </Chip>
  );
}

const TASK_CHIP: Record<string, { color: string; pulse: boolean }> = {
  queued: { color: "text-warn", pulse: false },
  running: { color: "text-info", pulse: true },
  done: { color: "text-ok", pulse: false },
  failed: { color: "text-danger", pulse: false },
};

export function TaskStateChip({ state }: { state: string }) {
  const cfg = TASK_CHIP[state] ?? { color: "text-faint", pulse: false };
  return (
    <Chip color={cfg.color} pulse={cfg.pulse}>
      {state}
    </Chip>
  );
}

const LANE_STYLES: Record<string, string> = {
  llamacpp: "text-lane-llamacpp border-lane-llamacpp/35 bg-lane-llamacpp/10",
  vllm: "text-lane-vllm border-lane-vllm/35 bg-lane-vllm/10",
  airllm: "text-lane-airllm border-lane-airllm/35 bg-lane-airllm/10",
};

export function LaneBadge({
  engine,
  detailed = false,
}: {
  engine: string;
  detailed?: boolean;
}) {
  // Registry lanes like "llamacpp-full-gpu"/"llamacpp-offload" share the
  // llamacpp color family.
  const family = engine.startsWith("llamacpp") ? "llamacpp" : engine;
  const label =
    engine === "airllm" && detailed ? "airllm · slow — chat only" : engine;
  return (
    <span
      className={cx(
        "inline-flex items-center rounded border px-1.5 py-0.5 font-mono text-[11px] font-medium",
        LANE_STYLES[family] ?? "border-border bg-raised text-muted",
      )}
    >
      {label}
    </span>
  );
}

// ── Skeleton & empty states ─────────────────────────────────────────────────

export function SkeletonBlock({ className }: { className?: string }) {
  return <div aria-hidden className={cx("skeleton", className)} />;
}

export function SkeletonList({ rows = 3 }: { rows?: number }) {
  return (
    <div aria-busy="true" aria-label="Loading" className="space-y-3">
      {Array.from({ length: rows }, (_, i) => (
        <SkeletonBlock key={i} className="h-20 w-full" />
      ))}
    </div>
  );
}

export function EmptyState({
  title,
  hint,
  action,
  icon = "box",
}: {
  title: string;
  hint?: string;
  action?: ReactNode;
  icon?: "box" | "spark" | "search";
}) {
  return (
    <div role="status" className="flex flex-col items-center px-6 py-12 text-center">
      <svg
        width="72"
        height="56"
        viewBox="0 0 72 56"
        fill="none"
        aria-hidden
        className="mb-4 text-edge"
      >
        {icon === "box" && (
          <>
            <path
              d="M36 6 62 19v18L36 50 10 37V19L36 6Z"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinejoin="round"
            />
            <path
              d="M10 19l26 13 26-13M36 32v18"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinejoin="round"
            />
            <circle cx="36" cy="24" r="3" fill="var(--color-accent)" opacity="0.7" />
          </>
        )}
        {icon === "spark" && (
          <>
            <path
              d="M36 4l5 14 15 5-15 5-5 14-5-14-15-5 15-5 5-14Z"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinejoin="round"
            />
            <circle cx="58" cy="42" r="4" stroke="var(--color-accent)" strokeWidth="2" opacity="0.8" />
            <circle cx="14" cy="44" r="2.5" stroke="currentColor" strokeWidth="2" />
          </>
        )}
        {icon === "search" && (
          <>
            <circle cx="32" cy="26" r="16" stroke="currentColor" strokeWidth="2" />
            <path d="m44 38 12 12" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
            <path
              d="M26 26c0-3.3 2.7-6 6-6"
              stroke="var(--color-accent)"
              strokeWidth="2"
              strokeLinecap="round"
              opacity="0.8"
            />
          </>
        )}
      </svg>
      <p className="text-sm font-medium text-text">{title}</p>
      {hint && <p className="mt-1 max-w-xs text-sm text-muted">{hint}</p>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

// ── Form primitives ─────────────────────────────────────────────────────────

const FIELD_CLASSES =
  "w-full rounded-md border border-edge bg-raised px-3 py-2.5 text-sm text-text placeholder:text-faint focus:border-accent focus:outline-none min-h-11";

export function Field({
  label,
  helper,
  children,
}: {
  label: string;
  helper?: ReactNode;
  children: (id: string) => ReactNode;
}) {
  const id = useId();
  return (
    <div className="space-y-1.5">
      <label htmlFor={id} className="block text-sm font-medium text-muted">
        {label}
      </label>
      {children(id)}
      {helper && <p className="text-xs text-faint">{helper}</p>}
    </div>
  );
}

export function TextInput({
  className,
  ...rest
}: InputHTMLAttributes<HTMLInputElement>) {
  return <input className={cx(FIELD_CLASSES, className)} {...rest} />;
}

export function TextArea({
  className,
  ...rest
}: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return <textarea className={cx(FIELD_CLASSES, "resize-y", className)} {...rest} />;
}

export function Select({
  className,
  children,
  ...rest
}: SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select className={cx(FIELD_CLASSES, "appearance-none", className)} {...rest}>
      {children}
    </select>
  );
}

export function Toggle({
  checked,
  onChange,
  label,
  disabled = false,
}: {
  checked: boolean;
  onChange: (next: boolean) => void;
  label: string;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={cx(
        "relative inline-flex h-11 w-14 shrink-0 cursor-pointer items-center disabled:cursor-not-allowed disabled:opacity-50",
      )}
    >
      <span
        className={cx(
          "h-6.5 w-12 rounded-full border transition-colors duration-200",
          checked ? "border-accent bg-accent/90" : "border-edge bg-overlay",
        )}
      />
      <span
        aria-hidden
        className={cx(
          "absolute top-1/2 h-5 w-5 -translate-y-1/2 rounded-full shadow transition-transform duration-200",
          checked ? "translate-x-6 bg-on-accent" : "translate-x-1 bg-muted",
        )}
      />
    </button>
  );
}

// ── Progress ────────────────────────────────────────────────────────────────

export function ProgressBar({
  pct,
  indeterminate = false,
  tone = "accent",
}: {
  pct: number | null;
  indeterminate?: boolean;
  tone?: "accent" | "info";
}) {
  const clamped = pct == null ? 0 : Math.min(100, Math.max(0, pct));
  return (
    <div
      role="progressbar"
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={indeterminate || pct == null ? undefined : Math.round(clamped)}
      className="h-1.5 w-full overflow-hidden rounded-full bg-overlay"
    >
      <div
        className={cx(
          "h-full rounded-full transition-[width] duration-500",
          tone === "accent" ? "bg-accent" : "bg-info",
          indeterminate && "animate-shimmer w-1/3",
        )}
        style={
          indeterminate
            ? {
                background:
                  "linear-gradient(90deg, transparent, var(--color-accent), transparent)",
                backgroundSize: "200% 100%",
                width: "100%",
              }
            : { width: `${clamped}%` }
        }
      />
    </div>
  );
}

// ── Sheet (bottom sheet on mobile, centered dialog on md+) ──────────────────

export function Sheet({
  open,
  onClose,
  title,
  children,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  children: ReactNode;
}) {
  const panelRef = useRef<HTMLDivElement>(null);
  const titleId = useId();

  useEffect(() => {
    if (!open) return;
    const el = panelRef.current;
    el?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = "";
    };
  }, [open, onClose]);

  if (!open) return null;
  return (
    <div className="fixed inset-0 z-40 flex items-end justify-center md:items-center">
      <button
        type="button"
        aria-label="Close"
        onClick={onClose}
        className="absolute inset-0 cursor-default bg-black/60 backdrop-blur-[2px]"
        tabIndex={-1}
      />
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
        className="relative z-10 max-h-[88dvh] w-full animate-rise overflow-y-auto rounded-t-2xl border border-border bg-surface p-5 pb-safe shadow-2xl shadow-black/50 outline-none md:max-w-lg md:rounded-2xl md:pb-5"
      >
        <div className="mx-auto mb-4 h-1 w-10 rounded-full bg-edge md:hidden" />
        <div className="mb-4 flex items-center justify-between gap-3">
          <h2 id={titleId} className="text-base font-semibold text-text">
            {title}
          </h2>
          <button
            type="button"
            aria-label="Close dialog"
            onClick={onClose}
            className="-m-2 cursor-pointer rounded-md p-2 text-muted hover:text-text"
          >
            <IconX size={18} />
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}

// ── Confirm dialog ──────────────────────────────────────────────────────────

export function ConfirmDialog({
  open,
  title,
  body,
  confirmLabel = "Delete",
  danger = true,
  busy = false,
  onCancel,
  onConfirm,
}: {
  open: boolean;
  title: string;
  body?: string;
  confirmLabel?: string;
  danger?: boolean;
  busy?: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  return (
    <Sheet open={open} onClose={onCancel} title={title}>
      {body && <p className="mb-5 text-sm text-muted">{body}</p>}
      <div className="flex gap-3">
        <Button className="flex-1" onClick={onCancel} disabled={busy}>
          Cancel
        </Button>
        <Button
          className="flex-1"
          variant={danger ? "danger" : "primary"}
          onClick={onConfirm}
          loading={busy}
        >
          {confirmLabel}
        </Button>
      </div>
    </Sheet>
  );
}

// ── Segmented tabs ──────────────────────────────────────────────────────────

export function SegmentedTabs<T extends string>({
  tabs,
  value,
  onChange,
}: {
  tabs: readonly { id: T; label: string }[];
  value: T;
  onChange: (next: T) => void;
}) {
  return (
    <div
      role="tablist"
      className="flex gap-1 rounded-lg border border-border bg-surface p-1"
    >
      {tabs.map((tab) => (
        <button
          key={tab.id}
          role="tab"
          type="button"
          aria-selected={value === tab.id}
          onClick={() => onChange(tab.id)}
          className={cx(
            "min-h-9 flex-1 cursor-pointer rounded-md px-2 text-sm font-medium transition-colors duration-150",
            value === tab.id
              ? "bg-overlay text-text shadow-sm"
              : "text-muted hover:text-text",
          )}
        >
          {tab.label}
        </button>
      ))}
    </div>
  );
}

// ── Collapsible ─────────────────────────────────────────────────────────────

export function Collapsible({
  summary,
  children,
  defaultOpen = false,
}: {
  summary: ReactNode;
  children: ReactNode;
  defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div>
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
        className="flex min-h-10 w-full cursor-pointer items-center gap-2 text-left text-sm text-muted hover:text-text"
      >
        {open ? (
          <IconChevronDown size={15} className="shrink-0" />
        ) : (
          <IconChevronRight size={15} className="shrink-0" />
        )}
        <span className="min-w-0 flex-1">{summary}</span>
      </button>
      {open && <div className="mt-1 pl-6">{children}</div>}
    </div>
  );
}

// ── Pull-to-refresh (touch devices, scrollTop === 0) ────────────────────────

export function PullToRefresh({
  onRefresh,
  refreshing,
  children,
}: {
  onRefresh: () => void;
  refreshing: boolean;
  children: ReactNode;
}) {
  const [pull, setPull] = useState(0);
  const startY = useRef<number | null>(null);
  const THRESHOLD = 70;

  const onTouchStart = useCallback((e: React.TouchEvent) => {
    const scroller = document.scrollingElement;
    if ((scroller?.scrollTop ?? 0) <= 0) {
      startY.current = e.touches[0].clientY;
    }
  }, []);

  const onTouchMove = useCallback((e: React.TouchEvent) => {
    if (startY.current == null) return;
    const delta = e.touches[0].clientY - startY.current;
    setPull(delta > 0 ? Math.min(delta * 0.45, 90) : 0);
  }, []);

  const onTouchEnd = useCallback(() => {
    if (pull >= THRESHOLD) onRefresh();
    startY.current = null;
    setPull(0);
  }, [pull, onRefresh]);

  return (
    <div
      onTouchStart={onTouchStart}
      onTouchMove={onTouchMove}
      onTouchEnd={onTouchEnd}
    >
      <div
        aria-hidden={!refreshing && pull === 0}
        className="flex items-center justify-center overflow-hidden text-muted transition-[height] duration-150"
        style={{ height: refreshing ? 40 : pull }}
      >
        {refreshing ? (
          <Spinner size={18} />
        ) : (
          <IconRefresh
            size={18}
            style={{ transform: `rotate(${pull * 3}deg)`, opacity: pull / THRESHOLD }}
          />
        )}
      </div>
      {children}
    </div>
  );
}
