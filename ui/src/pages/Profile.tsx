// The signed-in user's own profile: identity, avatar color, personal
// instructions, memory switch, password — plus log out.

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, errorMessage } from "../api/client";
import { IconBrain, IconChevronRight, IconLogout } from "../components/icons";
import { PageHeader } from "../components/layout";
import {
  Avatar,
  Button,
  Field,
  TextArea,
  TextInput,
  Toggle,
} from "../components/ui";
import { useToast } from "../hooks/toast";
import { clearAuth, setStoredUser, useCurrentUser } from "../lib/auth";
import { cx } from "../lib/utils";

// Mirrors services/user_service.py AVATAR_COLORS.
const AVATAR_COLORS = [
  "#f59e0b",
  "#22c55e",
  "#3b82f6",
  "#ec4899",
  "#8b5cf6",
  "#14b8a6",
  "#ef4444",
] as const;

function PasswordCard() {
  const { toast } = useToast();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");

  const change = useMutation({
    mutationFn: () => api.changeMyPassword(current, next),
    onSuccess: () => {
      toast("success", "Password changed");
      setCurrent("");
      setNext("");
    },
    onError: (err) => toast("error", errorMessage(err)),
  });

  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (current && next.length >= 6) change.mutate();
  };

  return (
    <form
      onSubmit={submit}
      className="space-y-3 rounded-xl border border-border bg-surface p-4"
    >
      <h2 className="text-sm font-semibold text-text">Password</h2>
      <Field label="Current password">
        {(id) => (
          <TextInput
            id={id}
            type="password"
            autoComplete="current-password"
            value={current}
            onChange={(e) => setCurrent(e.target.value)}
            required
          />
        )}
      </Field>
      <Field label="New password" helper="At least 6 characters.">
        {(id) => (
          <TextInput
            id={id}
            type="password"
            autoComplete="new-password"
            value={next}
            onChange={(e) => setNext(e.target.value)}
            minLength={6}
            required
          />
        )}
      </Field>
      <Button
        type="submit"
        variant="primary"
        loading={change.isPending}
        disabled={!current || next.length < 6}
      >
        Change password
      </Button>
    </form>
  );
}

export default function Profile() {
  const { toast } = useToast();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const cached = useCurrentUser();

  const me = useQuery({ queryKey: ["me"], queryFn: api.me });
  const user = me.data ?? cached;

  const [displayName, setDisplayName] = useState("");
  const [color, setColor] = useState("");
  const [instructions, setInstructions] = useState("");
  const [hydrated, setHydrated] = useState(false);

  useEffect(() => {
    if (user && !hydrated) {
      setDisplayName(user.display_name);
      setColor(user.avatar_color);
      setInstructions(user.personal_instructions);
      setHydrated(true);
    }
  }, [user, hydrated]);

  const patch = useMutation({
    mutationFn: (body: Parameters<typeof api.patchMe>[0]) => api.patchMe(body),
    onSuccess: (profile) => {
      setStoredUser(profile);
      queryClient.setQueryData(["me"], profile);
      toast("success", "Profile saved");
    },
    onError: (err) => toast("error", errorMessage(err)),
  });

  const toggleMemory = useMutation({
    mutationFn: (enabled: boolean) => api.patchMe({ memory_enabled: enabled }),
    onSuccess: (profile) => {
      setStoredUser(profile);
      queryClient.setQueryData(["me"], profile);
    },
    onError: (err) => toast("error", errorMessage(err)),
  });

  const logout = () => {
    clearAuth();
    navigate("/login", { replace: true });
  };

  if (!user) return null;

  const identityDirty =
    displayName !== user.display_name || color !== user.avatar_color;
  const instructionsDirty = instructions !== user.personal_instructions;

  return (
    <div>
      <PageHeader title="Profile" subtitle="How Forge knows you" />

      <div className="space-y-4">
        {/* Identity */}
        <section className="rounded-xl border border-border bg-surface p-4">
          <div className="mb-4 flex items-center gap-4">
            <Avatar
              name={displayName || user.username}
              color={color || user.avatar_color}
              size="lg"
            />
            <div className="min-w-0">
              <p className="flex items-center gap-2 text-base font-semibold text-text">
                <span className="truncate">{displayName || user.username}</span>
                {user.is_admin && (
                  <span className="shrink-0 rounded-full border border-accent/40 bg-accent/10 px-2 py-0.5 text-[10px] font-semibold tracking-wider text-accent uppercase">
                    Admin
                  </span>
                )}
              </p>
              <p className="font-mono text-sm text-muted">@{user.username}</p>
            </div>
          </div>

          <form
            onSubmit={(e) => {
              e.preventDefault();
              if (identityDirty) {
                patch.mutate({
                  display_name: displayName.trim(),
                  avatar_color: color,
                });
              }
            }}
            className="space-y-4"
          >
            <Field label="Display name">
              {(id) => (
                <TextInput
                  id={id}
                  value={displayName}
                  onChange={(e) => setDisplayName(e.target.value)}
                  placeholder={user.username}
                />
              )}
            </Field>
            <fieldset>
              <legend className="mb-2 block text-sm font-medium text-muted">
                Avatar color
              </legend>
              <div className="flex flex-wrap gap-2.5">
                {AVATAR_COLORS.map((c) => (
                  <button
                    key={c}
                    type="button"
                    aria-label={`Avatar color ${c}`}
                    aria-pressed={color === c}
                    onClick={() => setColor(c)}
                    className={cx(
                      "h-9 w-9 cursor-pointer rounded-full border-2 transition-transform duration-150 hover:scale-110",
                      color === c
                        ? "border-text"
                        : "border-transparent opacity-80",
                    )}
                    style={{ backgroundColor: c }}
                  />
                ))}
              </div>
            </fieldset>
            <Button
              type="submit"
              variant="primary"
              loading={patch.isPending}
              disabled={!identityDirty}
            >
              Save profile
            </Button>
          </form>
        </section>

        {/* Personal instructions */}
        <form
          onSubmit={(e) => {
            e.preventDefault();
            if (instructionsDirty) {
              patch.mutate({ personal_instructions: instructions });
            }
          }}
          className="space-y-3 rounded-xl border border-border bg-surface p-4"
        >
          <h2 className="text-sm font-semibold text-text">
            Personal instructions
          </h2>
          <Field
            label="What should the model know about you?"
            helper="Applies to all your chats — tone, context, standing preferences."
          >
            {(id) => (
              <TextArea
                id={id}
                rows={4}
                maxLength={4000}
                value={instructions}
                onChange={(e) => setInstructions(e.target.value)}
                placeholder="e.g. I'm a Rust developer; keep answers concise and skip beginner explanations."
              />
            )}
          </Field>
          <Button
            type="submit"
            variant="primary"
            loading={patch.isPending}
            disabled={!instructionsDirty}
          >
            Save instructions
          </Button>
        </form>

        {/* Memory */}
        <section className="rounded-xl border border-border bg-surface p-4">
          <div className="flex items-center justify-between gap-3">
            <div className="min-w-0">
              <h2 className="text-sm font-semibold text-text">Memory</h2>
              <p className="mt-0.5 text-sm text-muted">
                Let Forge remember useful things from your chats.
              </p>
            </div>
            <Toggle
              checked={user.memory_enabled}
              onChange={(next) => toggleMemory.mutate(next)}
              label="Memory"
              disabled={toggleMemory.isPending}
            />
          </div>
          <Link
            to="/memory"
            className="mt-3 flex min-h-11 items-center gap-2 rounded-lg border border-border bg-raised px-3 text-sm font-medium text-muted hover:text-text"
          >
            <IconBrain size={16} />
            <span className="flex-1">Review what Forge remembers</span>
            <IconChevronRight size={15} className="text-faint" />
          </Link>
        </section>

        <PasswordCard />

        <Button variant="danger" className="w-full" onClick={logout}>
          <IconLogout size={16} />
          Log out
        </Button>
      </div>
    </div>
  );
}
