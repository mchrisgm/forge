# Sandbox lane — smolvm microVM runtime

This image runs [smolvm](https://github.com/smol-machines/smolvm), an OCI-native
microVM runtime, as an HTTP control server. It is the execution backend for
Forge's "run this code" tool: the one place Forge runs code it did not write.
Every snippet runs inside a **hardware-isolated (KVM) microVM**, not a shared
container.

- **Compose service:** `smolvm` (profile `sandbox`), image `forge-smolvm`.
- **Start it:** `make sandbox` (builds and starts the profile).
- **Orchestrator client:** `orchestrator/app/services/sandbox.py`
- **REST surface:** `GET /api/sandbox/status`, `POST /api/sandbox/run`

## Requirements

- **`/dev/kvm` on the host.** smolvm boots real microVMs via libkrun/KVM. The
  compose service passes the device through (`devices: - /dev/kvm:/dev/kvm`).
  Without it, the container starts but cannot boot VMs. Verify with
  `ls -l /dev/kvm` and ensure the host user/container can read+write it.
- **x86_64 / glibc.** The pinned release is a glibc-linked `linux-x86_64`
  build, so the image is Debian-based (musl/Alpine would not load the libs).

## Security posture — read before changing anything

- **The control API has NO authentication.** `smolvm serve start` exposes full
  machine CRUD and command execution with no credentials. It is safe *only*
  because it lives on `forge-internal` and is never published or proxied by
  Caddy. **Do not** add a port mapping, put it behind the gateway, or move it to
  `forge-edge`. The orchestrator (also on `forge-internal`) is the only client.
- **Guest network is off.** The orchestrator creates its runner machine with
  `network: false`, so untrusted guest code has no egress. (The smolvm *host*
  still reaches a registry to pull base images; only the guest is severed.)
- **Timeouts + output caps** are enforced by the orchestrator client per run
  (hard timeout ≤ 60 s → SIGKILL / exit 124; ~40 KB per output stream).
- **Ephemeral per run.** Each execution uses a throwaway copy-on-write overlay
  off the pristine base image; no filesystem state survives a run or leaks
  between users.

## Pinned version

Pinned to **smolvm 1.8.3** (matches `Cargo.toml` `version` in the upstream
clone). Bump `ARG SMOLVM_VERSION` in the `Dockerfile` to upgrade.

### Checksum status

The Dockerfile downloads the pinned GitHub release tarball
(`smolvm-1.8.3-linux-x86_64.tar.gz`) **and** the release's `checksums.sha256`,
and verifies the tarball against it. Because the release binaries are unsigned,
this catches a corrupted download but not a mutated release. To hard-pin the
digest (recommended), follow the `TODO(security)` in the Dockerfile: record the
tarball's SHA-256 (computed once on a trusted machine) and switch the check to
compare against that constant. The upstream `scripts/install.sh` is the
fallback install method and documents the same archive layout.

## What the image does

1. **downloader stage** — fetches + checksum-verifies + unpacks the release
   tarball. The tarball bundles the `smolvm` wrapper, `smolvm-bin`, `lib/`
   (libkrun/libkrunfw), `agent-rootfs/`, and `init.krun` (the clone's `lib/`
   dir is Git-LFS pointers and is deliberately not used).
2. **runtime stage** — copies the unpacked distribution, puts `smolvm` on
   `PATH`, and stages `init.krun` at `/usr/local/share/smolvm/` (outside the
   `sandbox-state` volume, which masks `/var/lib/smolvm` at runtime).
3. Runs `smolvm serve start -l 0.0.0.0:9000` (`EXPOSE 9000`). Machine records
   and overlays persist on the `sandbox-state` volume at `/var/lib/smolvm`
   (`SMOLVM_DATA_DIR`).
