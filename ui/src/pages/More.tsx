import { Link, useNavigate } from "react-router-dom";
import {
  IconChevronRight,
  IconLogout,
  IconPlug,
  IconSliders,
  IconSparkles,
} from "../components/icons";
import { PageHeader } from "../components/layout";
import { clearToken } from "../lib/auth";

const LINKS = [
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
    hint: "Password, timeouts, schedules",
    icon: IconSliders,
  },
] as const;

export default function More() {
  const navigate = useNavigate();
  const logout = () => {
    clearToken();
    navigate("/login", { replace: true });
  };

  return (
    <div>
      <PageHeader title="More" />
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
