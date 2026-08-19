import { QueryClient, QueryClientProvider, useQuery } from "@tanstack/react-query";
import { useEffect, useState, type ReactNode } from "react";
import {
  createBrowserRouter,
  Navigate,
  RouterProvider,
  useNavigate,
} from "react-router-dom";
import { api, ApiError } from "./api/client";
import { IconFlame } from "./components/icons";
import { AppLayout } from "./components/layout";
import { Spinner } from "./components/ui";
import { EventsProvider } from "./hooks/events";
import { ToastProvider } from "./hooks/toast";
import { getToken, setStoredUser, UNAUTHORIZED_EVENT } from "./lib/auth";
import Chats from "./pages/Chats";
import Connectors from "./pages/Connectors";
import Login from "./pages/Login";
import Memory from "./pages/Memory";
import ModelChat from "./pages/ModelChat";
import Models from "./pages/Models";
import More from "./pages/More";
import Profile from "./pages/Profile";
import Register from "./pages/Register";
import SessionDetail from "./pages/SessionDetail";
import Sessions from "./pages/Sessions";
import Settings from "./pages/Settings";
import Setup from "./pages/Setup";
import Skills from "./pages/Skills";
import System from "./pages/System";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 5000,
      retry: (failureCount, error) => {
        if (error instanceof ApiError && error.status > 0 && error.status < 500) {
          return false;
        }
        return failureCount < 2;
      },
      refetchOnWindowFocus: true,
    },
  },
});

function BootSplash() {
  return (
    <div className="flex min-h-dvh flex-col items-center justify-center gap-4 bg-bg">
      <span className="flex h-14 w-14 items-center justify-center rounded-2xl bg-accent/15 text-accent">
        <IconFlame size={30} />
      </span>
      <Spinner size={18} />
    </div>
  );
}

/** Keeps the cached profile fresh while signed in. */
function ProfileSync() {
  const me = useQuery({
    queryKey: ["me"],
    queryFn: api.me,
    staleTime: 60_000,
  });
  useEffect(() => {
    if (me.data) setStoredUser(me.data);
  }, [me.data]);
  return null;
}

/**
 * Route guard, in order: setup_required → /setup; no token → /login; else app.
 */
function RequireAuth({ children }: { children: ReactNode }) {
  const navigate = useNavigate();
  const [authed, setAuthed] = useState(() => Boolean(getToken()));
  const status = useQuery({
    queryKey: ["auth-status"],
    queryFn: api.authStatus,
    staleTime: 30_000,
  });

  useEffect(() => {
    const onUnauthorized = () => {
      setAuthed(false);
      navigate("/login", { replace: true });
    };
    window.addEventListener(UNAUTHORIZED_EVENT, onUnauthorized);
    return () => window.removeEventListener(UNAUTHORIZED_EVENT, onUnauthorized);
  }, [navigate]);

  if (status.isLoading) return <BootSplash />;
  if (status.data?.setup_required) return <Navigate to="/setup" replace />;
  if (!authed) return <Navigate to="/login" replace />;
  return (
    <>
      <ProfileSync />
      {children}
    </>
  );
}

const router = createBrowserRouter([
  { path: "/setup", element: <Setup /> },
  { path: "/login", element: <Login /> },
  { path: "/register", element: <Register /> },
  {
    path: "/",
    element: (
      <RequireAuth>
        <EventsProvider>
          <AppLayout />
        </EventsProvider>
      </RequireAuth>
    ),
    children: [
      { index: true, element: <Navigate to="/chats" replace /> },
      { path: "chats/:conversationId?", element: <Chats /> },
      { path: "sessions", element: <Sessions /> },
      { path: "sessions/:sessionId", element: <SessionDetail /> },
      { path: "models", element: <Models /> },
      { path: "chat", element: <ModelChat /> },
      { path: "skills", element: <Skills /> },
      { path: "connectors", element: <Connectors /> },
      { path: "system", element: <System /> },
      { path: "settings", element: <Settings /> },
      { path: "profile", element: <Profile /> },
      { path: "memory", element: <Memory /> },
      { path: "more", element: <More /> },
      { path: "*", element: <Navigate to="/chats" replace /> },
    ],
  },
]);

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <ToastProvider>
        <RouterProvider router={router} />
      </ToastProvider>
    </QueryClientProvider>
  );
}
