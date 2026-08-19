import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useEffect, useState, type ReactNode } from "react";
import {
  createBrowserRouter,
  Navigate,
  RouterProvider,
  useNavigate,
} from "react-router-dom";
import { ApiError } from "./api/client";
import { AppLayout } from "./components/layout";
import { EventsProvider } from "./hooks/events";
import { ToastProvider } from "./hooks/toast";
import { getToken, UNAUTHORIZED_EVENT } from "./lib/auth";
import Connectors from "./pages/Connectors";
import Login from "./pages/Login";
import ModelChat from "./pages/ModelChat";
import Models from "./pages/Models";
import More from "./pages/More";
import SessionDetail from "./pages/SessionDetail";
import Sessions from "./pages/Sessions";
import Settings from "./pages/Settings";
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

function RequireAuth({ children }: { children: ReactNode }) {
  const navigate = useNavigate();
  const [authed, setAuthed] = useState(() => Boolean(getToken()));

  useEffect(() => {
    const onUnauthorized = () => {
      setAuthed(false);
      navigate("/login", { replace: true });
    };
    window.addEventListener(UNAUTHORIZED_EVENT, onUnauthorized);
    return () => window.removeEventListener(UNAUTHORIZED_EVENT, onUnauthorized);
  }, [navigate]);

  if (!authed) return <Navigate to="/login" replace />;
  return <>{children}</>;
}

const router = createBrowserRouter([
  { path: "/login", element: <Login /> },
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
      { index: true, element: <Navigate to="/sessions" replace /> },
      { path: "sessions", element: <Sessions /> },
      { path: "sessions/:sessionId", element: <SessionDetail /> },
      { path: "models", element: <Models /> },
      { path: "chat", element: <ModelChat /> },
      { path: "skills", element: <Skills /> },
      { path: "connectors", element: <Connectors /> },
      { path: "system", element: <System /> },
      { path: "settings", element: <Settings /> },
      { path: "more", element: <More /> },
      { path: "*", element: <Navigate to="/sessions" replace /> },
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
