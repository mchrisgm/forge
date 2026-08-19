#!/usr/bin/env bash
# Forge session-runner entrypoint (PLAN §6.3, §7).
#
# Started as root so it can fix ownership of the bind-mounted /workspace
# (the orchestrator creates the per-session dir as root on the workspaces
# volume), then immediately drops privileges to the non-root "forge" user
# (uid 1000) via gosu. Everything after the drop — git setup, optional clone,
# and the long-running `opencode serve` — runs as forge.
set -uo pipefail

FORGE_USER=forge
FORGE_UID=1000
WORKSPACE=/workspace

log() { printf '[entrypoint] %s\n' "$*" >&2; }

# ── Stage 1 (root): fix workspace ownership, drop privileges ────────────────
if [ "$(id -u)" = "0" ]; then
    mkdir -p "$WORKSPACE"
    owner="$(stat -c '%u' "$WORKSPACE" 2>/dev/null || echo '?')"
    if [ "$owner" != "$FORGE_UID" ]; then
        # Freshly created by the orchestrator (as root) the dir is empty, so
        # the recursive chown is instant; it only walks files on the rare
        # occasion a workspace was pre-populated by another uid.
        log "chowning $WORKSPACE (owner uid $owner -> $FORGE_UID)"
        chown -R "$FORGE_USER:$FORGE_USER" "$WORKSPACE" \
            || log "WARNING: chown of $WORKSPACE failed; continuing"
    fi
    # The orchestrator's file/git endpoints run `docker exec` WITHOUT a user,
    # i.e. as this image's default user (root). A default ACL on workspace
    # directories keeps any file such an exec creates writable by forge, so
    # the agent never gets locked out of its own workspace. New subdirs
    # inherit the default ACL automatically. Best-effort: some filesystems
    # lack ACL support.
    if command -v setfacl >/dev/null 2>&1; then
        find "$WORKSPACE" -type d -exec setfacl -m "d:u:${FORGE_USER}:rwX" {} + 2>/dev/null \
            || log "NOTE: default ACL on $WORKSPACE not set (fs without ACL support?)"
    fi
    # Configure the GitHub PAT at SYSTEM scope so both the forge agent and
    # root `docker exec` git pushes (orchestrator git endpoints) can use it.
    if [ -n "${GITHUB_PAT:-}" ]; then
        CREDENTIALS_FILE=/etc/forge-git-credentials
        ( umask 037 && printf 'https://x-access-token:%s@github.com\n' "$GITHUB_PAT" \
            > "$CREDENTIALS_FILE" )
        chown "root:$FORGE_USER" "$CREDENTIALS_FILE"
        git config --system credential.helper "store --file $CREDENTIALS_FILE"
        log "configured GitHub credential helper (system scope) for https pushes"
    fi
    log "dropping privileges to $FORGE_USER"
    exec gosu "$FORGE_USER:$FORGE_USER" "$0" "$@"
fi

# ── Stage 2 (forge) ─────────────────────────────────────────────────────────
export HOME=/home/forge
cd "$WORKSPACE" || { log "FATAL: cannot cd into $WORKSPACE"; exit 1; }

# (b) Write the rendered opencode.json for the container user.
CONFIG_DIR="$HOME/.config/opencode"
mkdir -p "$CONFIG_DIR"
if [ -n "${OPENCODE_CONFIG_CONTENT:-}" ]; then
    printf '%s' "$OPENCODE_CONFIG_CONTENT" > "$CONFIG_DIR/opencode.json"
    log "wrote $CONFIG_DIR/opencode.json"
else
    log "WARNING: OPENCODE_CONFIG_CONTENT not set; OpenCode will run with defaults"
fi

# (c) Git identity + GitHub credentials (before the clone, so private
# https://github.com/... repos clone with the PAT). The image bakes system-
# scope defaults (safe.directory, identity) so root `docker exec` git works
# too; the global-scope settings here are forge's own overridable defaults.
git config --global init.defaultBranch main
git config --global --add safe.directory "$WORKSPACE"
if [ -z "$(git config --global user.name 2>/dev/null || true)" ]; then
    git config --global user.name "forge"
fi
if [ -z "$(git config --global user.email 2>/dev/null || true)" ]; then
    git config --global user.email "forge@localhost"
fi
# Normally stage 1 already configured the credential helper at system scope;
# this fallback covers running the image directly as a non-root user.
if [ -n "${GITHUB_PAT:-}" ] && [ -z "$(git config --get credential.helper 2>/dev/null || true)" ]; then
    CREDENTIALS_FILE="$HOME/.git-credentials"
    git config --global credential.helper "store --file $CREDENTIALS_FILE"
    ( umask 077 && printf 'https://x-access-token:%s@github.com\n' "$GITHUB_PAT" \
        > "$CREDENTIALS_FILE" )
    log "configured GitHub credential helper (user scope) for https pushes"
fi

# (a) Clone the session repo into the (empty) workspace, failing gracefully.
if [ -n "${FORGE_REPO_URL:-}" ]; then
    if [ -z "$(ls -A "$WORKSPACE" 2>/dev/null)" ]; then
        log "cloning $FORGE_REPO_URL into $WORKSPACE"
        if git clone -- "$FORGE_REPO_URL" "$WORKSPACE"; then
            log "clone complete"
        else
            log "WARNING: clone of $FORGE_REPO_URL failed; continuing with empty workspace"
            # The workspace was empty before the attempt, so any partial clone
            # debris is safe to sweep out.
            find "$WORKSPACE" -mindepth 1 -maxdepth 1 -exec rm -rf {} + 2>/dev/null || true
        fi
    else
        log "workspace not empty; skipping clone of $FORGE_REPO_URL"
    fi
fi

# (d) Make sure the workspace is a git repo either way.
if ! git -C "$WORKSPACE" rev-parse --git-dir >/dev/null 2>&1; then
    log "initializing git repo in $WORKSPACE"
    git init "$WORKSPACE" >/dev/null || log "WARNING: git init failed"
fi

# (e) Hand off to OpenCode as PID 1.
PORT="${OPENCODE_PORT:-4096}"
log "starting opencode serve on 0.0.0.0:$PORT (session ${FORGE_SESSION_ID:-unknown})"
exec opencode serve --hostname 0.0.0.0 --port "$PORT"
