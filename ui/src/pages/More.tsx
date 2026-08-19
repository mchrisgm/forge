import { Link, useNavigate } from "react-router-dom";
import {
  IconBrain,
  IconChevronRight,
  IconLogout,
  IconPlug,
  IconSliders,
  IconSparkles,
} from "../components/icons";
import { PageHeader } from "../components/layout";
import { Avatar } from "../components/ui";
import { clearAuth, useCurrentUser } from "../lib/auth";

const LINKS = [
  {
    to: "/memory",
    label: "Memory",
    hint: "What Forge remembers about you",
    icon: IconBrain,
  },
  {
    to: "/skills",
    label: "Skills",
    hint: "Install & manage agent skills",
    icon: IconSparkles,
  },
  {
    to: "/connectors",
    label: "Connectors",
    hint: "MCP tools for sessions",
    icon: IconPlug,
  },
  {
    to: "/settings",
    label: "Settings",
    hint: "Timeouts, schedules, registration",
    icon: IconSliders,
  },
] as const;

export default function More() {
  const navigate = useNavigate();
  const user = useCurrentUser();
  const logout = () => {
    clearAuth();
    navigate("/login", { replace: true });
  };

  return (
    <div>
      <PageHeader title="More" />

      {user && (
        <Link
          to="/profile"
          className="mb-4 flex min-h-16 items-center gap-3 rounded-xl border border-border bg-surface px-4 hover:bg-raised"
        >
          <Avatar
            name={user.display_name || user.username}
            color={user.avatar_color}
          />
          <span className="min-w-0 flex-1">
            <span className="flex items-center gap-2 text-sm font-medium text-text">
              <span className="truncate">
                {user.display_name || user.username}
              </span>
              {user.is_admin && (
                <span className="shrink-0 rounded-full border border-accent/40 bg-accent/10 px-1.5 py-px text-[9px] font-semibold tracking-wider text-accent uppercase">
                  Admin
                </span>
              )}
            </span>
            <span className="block font-mono text-xs text-faint">
              @{user.username}
            </span>
          </span>
          <IconChevronRight size={16} className="text-faint" />
        </Link>
      )}

      <ul className="overflow-hidden rounded-xl border border-border bg-surface">
        {LINKS.map(({ to, label, hint, icon: Icon }) => (
          <li key={to} className="border-b border-border last:border-none">
            <Link
              to={to}
              className="flex min-h-14 items-center gap-3 px-4 hover:bg-raised"
            >
              <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-raised text-muted">
                <Icon size={18} />
              </span>
              <span className="min-w-0 flex-1">
                <span className="block text-sm font-medium text-text">
                  {label}
                </span>
                <span className="block text-xs text-faint">{hint}</span>
              </span>
              <IconChevronRight size={16} className="text-faint" />
            </Link>
          </li>
        ))}
        <li>
          <button
            type="button"
            onClick={logout}
            className="flex min-h-14 w-full cursor-pointer items-center gap-3 px-4 text-left hover:bg-raised"
          >
            <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-danger/10 text-danger">
              <IconLogout size={18} />
            </span>
            <span className="text-sm font-medium text-danger">Log out</span>
          </button>
        </li>
      </ul>
      <p className="mt-6 text-center text-xs text-faint">Forge v0.1.0</p>
    </div>
  );
}
