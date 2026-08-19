# syntax=docker/dockerfile:1
#
# forge-smolvm — the sandbox lane's microVM runtime.
#
# smolvm is an OCI-native microVM runtime (github.com/smol-machines/smolvm). We
# run its HTTP control API (`smolvm serve start`) so the orchestrator can boot
# hardware-isolated (KVM) microVMs to execute untrusted "run this code" snippets.
#
# SECURITY POSTURE (see README.md):
#   * The control API has NO authentication. This container MUST stay on
#     forge-internal and MUST NOT be published or placed behind Caddy.
#   * The host needs /dev/kvm (passed through in docker-compose.yml).
#
# The release binaries are glibc-linked and the bundled libkrun/libkrunfw libs
# ship *inside* the release tarball (the clone's lib/ dir is Git-LFS pointers —
# not usable), so we fetch the real GitHub release archive here rather than
# building from source. Debian (glibc), not Alpine (musl), is required.
#
# Pinned to the version in the clone's Cargo.toml.

# ── downloader stage: fetch + verify + unpack the pinned release ─────────────
FROM debian:bookworm-slim AS downloader

# Pin: keep in sync with smol-machines/smolvm Cargo.toml `version`.
ARG SMOLVM_VERSION=1.8.3
ARG SMOLVM_PLATFORM=linux-x86_64
ARG SMOLVM_BASE_URL=https://github.com/smol-machines/smolvm/releases/download

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl tar \
    && rm -rf /var/lib/apt/lists/*

# Fetch the tarball and the release's checksums.sha256, then verify the tarball
# against it (integrity: guards a corrupted/partial download).
#
# TODO(security): hard-pin the release. Because the release binaries are
# unsigned, verifying against a checksums file fetched from the same release
# only catches corruption, not a mutated release. Once, on a trusted machine:
#   curl -fSL "$SMOLVM_BASE_URL/v${SMOLVM_VERSION}/smolvm-${SMOLVM_VERSION}-linux-x86_64.tar.gz" \
#     | sha256sum
# then record the digest below and switch the check to compare against it:
#   ARG SMOLVM_SHA256=<paste-64-hex-digest-here>
#   echo "${SMOLVM_SHA256}  ${TARBALL}" | sha256sum -c -
RUN set -eux; \
    cd /tmp; \
    TARBALL="smolvm-${SMOLVM_VERSION}-${SMOLVM_PLATFORM}.tar.gz"; \
    REL="${SMOLVM_BASE_URL}/v${SMOLVM_VERSION}"; \
    curl -fSL "${REL}/${TARBALL}" -o "${TARBALL}"; \
    curl -fSL "${REL}/checksums.sha256" -o checksums.sha256; \
    awk -v f="${TARBALL}" '$2 == f { print }' checksums.sha256 > tarball.sha256; \
    test -s tarball.sha256; \
    sha256sum -c tarball.sha256; \
    mkdir -p /opt/smolvm; \
    tar -xzf "${TARBALL}" -C /opt/smolvm; \
    d="$(find /opt/smolvm -maxdepth 1 -type d -name 'smolvm-*' | head -1)"; \
    test -n "${d}"; \
    mv "${d}" /opt/smolvm/dist; \
    test -x /opt/smolvm/dist/smolvm-bin; \
    test -d /opt/smolvm/dist/lib

# ── runtime stage: just the runtime + the unpacked distribution ──────────────
FROM debian:bookworm-slim

# ca-certificates: smolvm pulls OCI base images over TLS on the host side.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# The self-contained distribution: the `smolvm` wrapper points LD_LIBRARY_PATH
# at ./lib and SMOLVM_AGENT_ROOTFS at ./agent-rootfs, so keeping the tree intact
# is all that's needed for the libs and agent rootfs to resolve.
COPY --from=downloader /opt/smolvm/dist /opt/smolvm/dist

# Put `smolvm` on PATH and stage init.krun in a system location the runtime
# probes (/usr/local/share/smolvm) — it must live OUTSIDE /var/lib/smolvm, which
# the sandbox-state volume masks at runtime. Templates stay co-located in dist.
RUN set -eux; \
    ln -s /opt/smolvm/dist/smolvm /usr/local/bin/smolvm; \
    mkdir -p /usr/local/share/smolvm /var/lib/smolvm; \
    if [ -f /opt/smolvm/dist/init.krun ]; then \
        cp /opt/smolvm/dist/init.krun /usr/local/share/smolvm/init.krun; \
        chmod 0755 /usr/local/share/smolvm/init.krun; \
    fi

# serve auto-defaults its data root to /var/lib/smolvm when privileged; set it
# explicitly so machine records + overlays land on the sandbox-state volume.
ENV SMOLVM_DATA_DIR=/var/lib/smolvm

# Control API port (kept on forge-internal; never published).
EXPOSE 9000

# No auth by design — safe only because this stays on the internal network.
CMD ["smolvm", "serve", "start", "-l", "0.0.0.0:9000"]
