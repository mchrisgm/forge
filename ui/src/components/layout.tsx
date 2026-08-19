import type { ReactNode } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";
import { useGlobalEvents } from "../hooks/events";
import { useCurrentUser } from "../lib/auth";
import { cx } from "../lib/utils";
import {
  IconActivity,
  IconAlert,
  IconBrain,
  IconChat,
  IconCube,
  IconDots,
  IconFlame,
  IconPlug,
  IconSliders,
  IconSparkles,
  IconTerminal,
} from "./icons";
import { Avatar } from "./ui";

const PRIMARY_TABS = [
  { to: "/chats", label: "Chat", icon: IconChat },
  { to: "/sessions", label: "Sessions", icon: IconTerminal },
  { to: "/models", label: "Models", icon: IconCube },
  { to: "/system", label: "System", icon: IconActivity },
  { to: "/more", label: "More", icon: IconDots },
] as const;

const SIDEBAR_LINKS = [
  { to: "/chats", label: "Chat", icon: IconChat },
  { to: "/sessions", label: "Sessions", icon: IconTerminal },
  { to: "/models", label: "Models", icon: IconCube },
  { to: "/skills", label: "Skills", icon: IconSparkles },
  { to: "/connectors", label: "Connectors", icon: IconPlug },
  { to: "/memory", label: "Memory", icon: IconBrain },
  { to: "/system", label: "System", icon: IconActivity },
  { to: "/settings", label: "Settings", icon: IconSliders },
] as const;

function ConnectionBanner() {
  const { connected } = useGlobalEvents();
  if (connected) return null;
  return (
    <div
      role="alert"
      className="sticky top-0 z-30 flex items-center justify-center gap-2 border-b border-warn/30 bg-warn/10 px-4 py-2 pt-safe text-xs font-medium text-warn"
    >
      <IconAlert size={14} />
      Connection lost — retrying…
    </div>
  );
}

function SidebarUserChrome() {
  const user = useCurrentUser();
  if (!user) return null;
  return (
    <NavLink
      to="/profile"
      className={({ isActive }) =>
        cx(
          "mx-3 mb-4 flex min-h-12 items-center gap-2.5 rounded-lg px-2.5 transition-colors duration-150",
          isActive
            ? "bg-accent/10 text-accent"
            : "text-muted hover:bg-raised hover:text-text",
        )
      }
    >
      <Avatar name={user.display_name || user.username} color={user.avatar_color} size="sm" />
      <span className="min-w-0 flex-1">
        <span className="block truncate text-sm font-medium text-text">
          {user.display_name || user.username}
        </span>
        <span className="block text-[11px] text-faint">
          {user.is_admin ? "Admin · view profile" : "View profile"}
        </span>
      </span>
    </NavLink>
  );
}

export function AppLayout() {
  const location = useLocation();
  // Chat surfaces pin their own composer to the bottom edge, so they manage
  // bottom spacing and hide the tab bar. /chats (the conversation list on
  // mobile) keeps the tab bar; an open conversation (/chats/…) hides it.
  const inChatSurface =
    /^\/sessions\/[^/]+/.test(location.pathname) ||
    /^\/chats\/[^/]+/.test(location.pathname) ||
    location.pathname === "/chat";
  // The Chat section runs a two-pane layout that wants the full width.
  const isChatSection = /^\/chats(\/|$)/.test(location.pathname);

  return (
    <div className="min-h-dvh bg-bg">
      {/* Sidebar (md+) */}
      <aside className="fixed inset-y-0 left-0 z-20 hidden w-56 flex-col border-r border-border bg-surface md:flex">
        <div className="flex items-center gap-2.5 px-5 pt-6 pb-8">
          <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-accent/15 text-accent">
            <IconFlame size={18} />
          </span>
          <span className="text-lg font-bold tracking-tight text-text">
            Forge
          </span>
        </div>
        <nav aria-label="Main" className="flex flex-col gap-1 px-3">
          {SIDEBAR_LINKS.map(({ to, label, icon: Icon }) => (
            <NavLink
              key={to}
              to={to}
              className={({ isActive }) =>
                cx(
                  "flex min-h-11 items-center gap-3 rounded-lg px-3 text-sm font-medium transition-colors duration-150",
                  isActive
                    ? "bg-accent/10 text-accent"
                    : "text-muted hover:bg-raised hover:text-text",
                )
              }
            >
              <Icon size={18} />
              {label}
            </NavLink>
          ))}
        </nav>
        <div className="mt-auto">
          <SidebarUserChrome />
        </div>
      </aside>

      {/* Main column */}
      <div className="md:pl-56">
        <ConnectionBanner />
        <main
          className={cx(
            "mx-auto w-full pt-safe",
            isChatSection ? "max-w-6xl" : "max-w-4xl px-4 md:px-8",
            inChatSurface || isChatSection ? "pb-0" : "pb-tabbar md:pb-10",
          )}
        >
          <Outlet />
        </main>
      </div>

      {/* Bottom tab bar (mobile) */}
      <nav
        aria-label="Main"
        className={cx(
          "fixed inset-x-0 bottom-0 z-20 border-t border-border bg-surface/95 backdrop-blur md:hidden",
          inChatSurface && "hidden",
        )}
      >
        <div className="flex pb-safe">
          {PRIMARY_TABS.map(({ to, label, icon: Icon }) => (
            <NavLink
              key={to}
              to={to}
              className={({ isActive }) =>
                cx(
                  "flex min-h-14 flex-1 flex-col items-center justify-center gap-0.5 text-[10px] font-medium transition-colors duration-150",
                  isActive ? "text-accent" : "text-faint hover:text-muted",
                )
              }
            >
              <Icon size={21} />
              {label}
            </NavLink>
          ))}
        </div>
      </nav>
    </div>
  );
}

export function PageHeader({
  title,
  subtitle,
  actions,
}: {
  title: string;
  subtitle?: string;
  actions?: ReactNode;
}) {
  return (
    <header className="mb-5 flex items-end justify-between gap-3 pt-6">
      <div className="min-w-0">
        <h1 className="truncate text-xl font-bold tracking-tight text-text">
          {title}
        </h1>
        {subtitle && <p className="mt-0.5 text-sm text-muted">{subtitle}</p>}
      </div>
      {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
    </header>
  );
}
