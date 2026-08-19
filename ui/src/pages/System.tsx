import { useQuery } from "@tanstack/react-query";
import { api, errorMessage } from "../api/client";
import { IconAlert } from "../components/icons";
import { PageHeader } from "../components/layout";
import {
  Button,
  Chip,
  EmptyState,
  LaneBadge,
  SkeletonList,
} from "../components/ui";
import { cx, formatGb } from "../lib/utils";

function Semicircle({
  label,
  used,
  total,
  unit = "GB",
  marker,
  tone = "info",
}: {
  label: string;
  used: number;
  total: number;
  unit?: string;
  marker?: number | null;
  tone?: "info" | "accent" | "ok";
}) {
  const pct = total > 0 ? Math.min(1, used / total) : 0;
  const R = 44;
  const C = Math.PI * R; // semicircle arc length
  const colors = { info: "var(--color-info)", accent: "var(--color-accent)", ok: "var(--color-ok)" };
  const markerAngle = marker != null && total > 0 ? Math.min(1, marker / total) * Math.PI : null;

  return (
    <div className="flex flex-col items-center rounded-xl border border-border bg-surface p-4">
      <svg
        width="120"
        height="70"
        viewBox="0 0 120 70"
        role="img"
        aria-label={`${label}: ${used.toFixed(1)} of ${total.toFixed(1)} ${unit}`}
      >
        <path
          d="M 16 62 A 44 44 0 0 1 104 62"
          fill="none"
          stroke="var(--color-overlay)"
          strokeWidth="10"
          strokeLinecap="round"
        />
        <path
          d="M 16 62 A 44 44 0 0 1 104 62"
          fill="none"
          stroke={colors[tone]}
          strokeWidth="10"
          strokeLinecap="round"
          strokeDasharray={`${C * pct} ${C}`}
          style={{ transition: "stroke-dasharray 0.6s ease" }}
        />
        {markerAngle != null && (
          <line
            x1={60 - (R - 9) * Math.cos(markerAngle)}
            y1={62 - (R - 9) * Math.sin(markerAngle)}
            x2={60 - (R + 9) * Math.cos(markerAngle)}
            y2={62 - (R + 9) * Math.sin(markerAngle)}
            stroke="var(--color-warn)"
            strokeWidth="2.5"
          />
        )}
        <text
          x="60"
          y="56"
          textAnchor="middle"
          fill="var(--color-text)"
          fontSize="15"
          fontWeight="700"
          fontFamily="var(--font-mono)"
        >
          {used.toFixed(1)}
        </text>
        <text
          x="60"
          y="68"
          textAnchor="middle"
          fill="var(--color-faint)"
          fontSize="9"
        >
          of {total.toFixed(1)} {unit}
        </text>
      </svg>
      <p className="mt-1 text-xs font-medium text-muted">{label}</p>
    </div>
  );
}

export default function System() {
  const stats = useQuery({
    queryKey: ["system"],
    queryFn: api.systemStats,
    refetchInterval: 5000, // paused automatically while the tab is hidden
  });

  if (stats.isLoading) {
    return (
      <div>
        <PageHeader title="System" subtitle="Host resources and containers" />
        <SkeletonList rows={4} />
      </div>
    );
  }

  if (stats.isError || !stats.data) {
    return (
      <div>
        <PageHeader title="System" subtitle="Host resources and containers" />
        <EmptyState
          icon="search"
          title="Couldn't load system stats"
          hint={stats.error ? errorMessage(stats.error) : undefined}
          action={<Button onClick={() => void stats.refetch()}>Retry</Button>}
        />
      </div>
    );
  }

  const s = stats.data;
  const lease = s.engine.lease;

  return (
    <div>
      <PageHeader title="System" subtitle="Host resources and containers" />

      {!s.docker_ok && (
        <div
          role="alert"
          className="mb-4 flex items-center gap-2 rounded-xl border border-danger/40 bg-danger/10 px-4 py-3 text-sm text-danger"
        >
          <IconAlert size={16} className="shrink-0" />
          Docker socket unreachable — engines and sessions cannot be managed.
        </div>
      )}

      {/* Gauges */}
      <div className="mb-5 grid grid-cols-2 gap-3 sm:grid-cols-4">
        {s.gpu ? (
          <Semicircle
            label="VRAM"
            used={s.gpu.vram_used_gb}
            total={s.gpu.vram_total_gb}
            marker={s.budgets.vram_gb}
            tone="accent"
          />
        ) : (
          <div className="flex flex-col items-center justify-center rounded-xl border border-dashed border-border p-4 text-center">
            <p className="text-sm font-medium text-muted">No GPU</p>
            <p className="mt-1 text-xs text-faint">pynvml found no device</p>
          </div>
        )}
        <Semicircle label="RAM" used={s.ram.used_gb} total={s.ram.total_gb} />
        {s.disk ? (
          <Semicircle
            label="Disk (models)"
            used={s.disk.used_gb}
            total={s.disk.total_gb}
            tone="ok"
          />
        ) : (
          <div className="flex flex-col items-center justify-center rounded-xl border border-dashed border-border p-4 text-center">
            <p className="text-sm font-medium text-muted">Disk n/a</p>
          </div>
        )}
        <div className="flex flex-col items-center justify-center rounded-xl border border-border bg-surface p-4">
          <p className="font-mono text-2xl font-bold text-text">
            {Math.round(s.cpu_pct)}
            <span className="text-sm text-faint">%</span>
          </p>
          <p className="mt-1 text-xs font-medium text-muted">CPU</p>
          {s.gpu && (
            <p className="mt-1.5 max-w-full truncate text-[10px] text-faint">
              {s.gpu.name}
            </p>
          )}
        </div>
      </div>

      {/* Engine lease */}
      <section className="mb-5 rounded-xl border border-border bg-surface p-4">
        <h2 className="mb-2 text-sm font-semibold text-muted">GPU lease</h2>
        {lease ? (
          <div className="flex flex-wrap items-center gap-3">
            <span
              aria-hidden
              className={cx(
                "h-2.5 w-2.5 rounded-full",
                lease.state === "ready" && "bg-ok",
                lease.state === "starting" && "animate-pulse-dot bg-warn text-warn",
                lease.state === "failed" && "bg-danger",
              )}
            />
            <span className="text-sm font-semibold text-text">
              {lease.model_name}
            </span>
            <LaneBadge engine={lease.engine} />
            <span className="text-xs text-muted">{lease.state}</span>
            {s.gpu && lease.state === "ready" && (
              <span className="ml-auto font-mono text-xs text-faint">
                {formatGb(s.gpu.vram_used_gb)} VRAM · {s.gpu.utilization_pct}% util
              </span>
            )}
          </div>
        ) : (
          <p className="text-sm text-muted">
            GPU is free — load a model from the Models page.
          </p>
        )}
      </section>

      {/* Session containers */}
      <section className="rounded-xl border border-border bg-surface p-4">
        <h2 className="mb-2 text-sm font-semibold text-muted">
          Session containers
        </h2>
        {s.session_containers.length === 0 ? (
          <p className="text-sm text-muted">No session containers running.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border text-left text-xs text-faint">
                  <th className="py-2 pr-4 font-medium">Container</th>
                  <th className="py-2 font-medium">Status</th>
                </tr>
              </thead>
              <tbody>
                {s.session_containers.map((c) => (
                  <tr key={c.name} className="border-b border-border/50 last:border-none">
                    <td className="py-2 pr-4 font-mono text-xs text-text">
                      {c.name}
                    </td>
                    <td className="py-2">
                      <Chip
                        color={c.status === "running" ? "text-ok" : "text-faint"}
                        pulse={c.status === "running"}
                      >
                        {c.status}
                      </Chip>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
