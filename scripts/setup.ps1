#Requires -Version 5.1
<#
.SYNOPSIS
  Forge first-run setup (Windows / PowerShell).

.DESCRIPTION
  One command from a fresh clone to a running stack — the Windows peer of
  scripts/setup.sh, for hosts with Docker Desktop but no `make`:
    1. preflight the host (Docker + compose v2 are required; GPU/KVM warn)
    2. create .env from .env.example and generate its secrets (idempotent)
    3. auto-include the GPU overlay when an NVIDIA GPU is present
    4. build + start the stack, build the session/engine images, prefetch the
       big engine images

  Re-runnable: an existing .env is left alone and compose reconciles in place.

.PARAMETER Sandbox
  Also build + start the smolvm sandbox lane. NOTE: smolvm needs /dev/kvm, which
  Docker Desktop on Windows does not expose — the sandbox lane is effectively
  Linux-host only. Left off by default here.

.PARAMETER SkipPull
  Skip prefetching the llama.cpp/vLLM engine images.

.PARAMETER NoGpu
  Force CPU-only bring-up even if an NVIDIA GPU is detected.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
#>
[CmdletBinding()]
param(
  [switch]$Sandbox,
  [switch]$SkipPull,
  [switch]$NoGpu
)

$ErrorActionPreference = 'Stop'

$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

function Write-Section($text) { Write-Host "`n$text" -ForegroundColor Cyan }
function Write-Info($text)    { Write-Host "  $text" }
function Stop-Setup($text) {
  Write-Host "`nSetup aborted: $text" -ForegroundColor Red
  exit 1
}

# Windows PowerShell 5.1 wraps a native command's stderr in an ErrorRecord as
# soon as that stream is redirected, so a benign `docker info` warning becomes a
# terminating error under $ErrorActionPreference = 'Stop'. Silent probes run
# through here: stderr is merged into the pipeline and dropped, and only the
# exit code comes back.
#
# Both helpers are deliberately *simple* functions reading $args. An advanced
# function (one carrying [Parameter()] or [CmdletBinding()]) also gains the
# common parameters, and PowerShell would bind a docker flag such as -d to
# -Debug rather than passing it through -- which silently turned
# `docker compose up -d` into an attached, foreground bring-up.
function Invoke-Quiet {
  $exe = $args[0]
  $rest = @()
  if ($args.Length -gt 1) { $rest = $args[1..($args.Length - 1)] }
  $prev = $ErrorActionPreference
  $ErrorActionPreference = 'Continue'
  try { & $exe @rest 2>&1 | Out-Null } finally { $ErrorActionPreference = $prev }
  return $LASTEXITCODE
}

# Capture a native command's stdout with stderr suppressed; $null when it
# exits non-zero. Same 5.1 stderr/ErrorRecord trap as Invoke-Quiet applies.
function Invoke-Capture {
  $exe = $args[0]
  $rest = @()
  if ($args.Length -gt 1) { $rest = $args[1..($args.Length - 1)] }
  $prev = $ErrorActionPreference
  $ErrorActionPreference = 'Continue'
  try { $out = & $exe @rest 2>&1 } finally { $ErrorActionPreference = $prev }
  if ($LASTEXITCODE -ne 0) { return $null }
  $out | Where-Object { $_ -isnot [System.Management.Automation.ErrorRecord] } | ForEach-Object { "$_" }
}

# Run an external command and abort if it exits non-zero.
function Invoke-Checked {
  $exe = $args[0]
  $rest = @()
  if ($args.Length -gt 1) { $rest = $args[1..($args.Length - 1)] }
  & $exe @rest
  if ($LASTEXITCODE -ne 0) {
    Stop-Setup "command failed: $($args -join ' ')"
  }
}

# ── 1. preflight ────────────────────────────────────────────────────────────
Write-Section '[1/6] Checking host prerequisites'
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
  Stop-Setup 'docker not found — install Docker Desktop: https://docs.docker.com/desktop/install/windows-install/'
}
if ((Invoke-Quiet docker info) -ne 0) {
  Stop-Setup 'the Docker daemon is unreachable — start Docker Desktop and wait for it to report "running", then re-run this script'
}
if ((Invoke-Quiet docker compose version) -ne 0) {
  Stop-Setup 'docker compose v2 not found — update Docker Desktop (it bundles compose v2)'
}
Write-Info 'docker + compose v2 OK'

$hasGpu = [bool](Get-Command nvidia-smi -ErrorAction SilentlyContinue)
if ($hasGpu) {
  Write-Info 'NVIDIA GPU detected (make sure Docker Desktop GPU support is enabled in Settings > Resources)'
} else {
  Write-Info 'no NVIDIA GPU detected — CPU-only bring-up; the stack runs but engine loads need a GPU'
}

# ── 2. .env + secrets ───────────────────────────────────────────────────────
Write-Section '[2/6] Preparing .env'
if (-not (Test-Path .env)) {
  Copy-Item .env.example .env
  Write-Info 'created .env from .env.example'
} else {
  Write-Info '.env already exists — leaving it untouched'
}

function New-Secret {
  $bytes = New-Object 'System.Byte[]' 32
  [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
  -join ($bytes | ForEach-Object { $_.ToString('x2') })
}

# Set KEY to a fresh secret only when it is missing or blank in .env.
function Set-EnvSecret([string]$key) {
  $lines = @(Get-Content .env)
  # "filled" = the key already has a non-whitespace, non-comment value.
  $filled = $lines | Where-Object { $_ -match "^$key=[^\s#]" }
  if ($filled) { return }
  $value = New-Secret
  $kept = $lines | Where-Object { $_ -notmatch "^$key=" }
  # Rewrite as UTF-8 without a BOM and with LF endings (compose/dotenv choke
  # on a BOM, and LF avoids CRLF sneaking into values).
  $out = (@($kept) + "$key=$value") -join "`n"
  [System.IO.File]::WriteAllText((Join-Path $RepoRoot '.env'), $out + "`n")
  Write-Info "generated $key"
}
Set-EnvSecret 'SEARXNG_SECRET'
Set-EnvSecret 'FORGE_SECRET_KEY'

# ── 3. GPU overlay detection ────────────────────────────────────────────────
Write-Section '[3/6] Selecting compose files'
$composeFiles = @('-f', 'docker-compose.yml')
if ((-not $NoGpu) -and $hasGpu) {
  $composeFiles += @('-f', 'docker-compose.gpu.yml')
  Write-Info 'including docker-compose.gpu.yml (GPU stats + leases)'
} else {
  Write-Info 'CPU-only bring-up (no GPU overlay)'
}

# ── 4. build + start ────────────────────────────────────────────────────────
Write-Section '[4/6] Building and starting the stack'
Invoke-Checked docker compose @composeFiles up -d --build
# Profile-gated images are invisible to a plain `up` — build them so the
# orchestrator can spawn sessions and the local engine lanes.
Invoke-Checked docker compose @composeFiles --profile build-only build session-runner
Invoke-Checked docker compose @composeFiles --profile engines build airllm imagegen
if (-not $SkipPull) {
  Write-Info 'prefetching the llama.cpp/vLLM engine images (skip next time with -SkipPull)...'
  docker compose @composeFiles --profile engines pull llamacpp vllm sglang tabby
  if ($LASTEXITCODE -ne 0) {
    Write-Info "engine image prefetch skipped/failed — they'll pull on first model load"
  }
}

# Engine and session containers are spawned by the orchestrator through the
# docker socket, never by compose, so `up -d --build` recreates nothing for
# them: a container still running a pre-rebuild image keeps serving old code,
# and on boot the orchestrator re-adopts it blindly (reconcile_on_boot never
# compares image IDs). Remove any lane container whose image no longer
# matches its freshly built tag, then restart the orchestrator so it drops
# the now-dead leases (it never respawns or health-checks them on its own).
Write-Info 'sweeping orchestrator-spawned containers left on outdated images...'
$staleRemoved = @()
foreach ($label in 'forge.engine', 'forge.session') {
  foreach ($id in @(Invoke-Capture docker ps -q --filter "label=$label")) {
    if (-not $id) { continue }
    $name      = (Invoke-Capture docker inspect -f '{{.Name}}' $id) -replace '^/', ''
    $ref       = Invoke-Capture docker inspect -f '{{.Config.Image}}' $id
    $runningId = Invoke-Capture docker inspect -f '{{.Image}}' $id
    $currentId = $null
    if ($ref) { $currentId = Invoke-Capture docker image inspect -f '{{.Id}}' "$ref" }
    if (-not $currentId -or "$runningId" -ne "$currentId") {
      $null = Invoke-Quiet docker rm -f $id
      $staleRemoved += $name
      Write-Info "removed $name (its image was rebuilt; the container ran the old build)"
    }
  }
}
# Engine-profile services started via a stray `compose --profile engines up`
# duplicate the orchestrator's lanes and pin a GPU it wants to lease. compose
# ps only matches compose-created containers (runtime labels), never the
# orchestrator's forge-engine-* ones, so this cannot touch a healthy lane.
foreach ($id in @(Invoke-Capture docker compose @composeFiles --profile engines ps -q llamacpp vllm sglang tabby airllm imagegen)) {
  if (-not $id) { continue }
  $name = (Invoke-Capture docker inspect -f '{{.Name}}' $id) -replace '^/', ''
  $null = Invoke-Quiet docker rm -f $id
  $staleRemoved += $name
  Write-Info "removed $name (engine lanes belong to the orchestrator, not compose up)"
}
if ($staleRemoved.Count -gt 0) {
  Invoke-Checked docker compose @composeFiles restart orchestrator
  Write-Info 'orchestrator restarted to drop stale leases - re-load models from the Models page'
} else {
  Write-Info 'none found - all lanes current'
}

# ── 5. optional sandbox lane ────────────────────────────────────────────────
Write-Section '[5/6] Optional sandbox lane'
if ($Sandbox) {
  Write-Info 'WARNING: smolvm microVMs need /dev/kvm, which Docker Desktop on Windows does not expose — the sandbox lane will not run here. Use a Linux host for the code-run sandbox.'
  Invoke-Checked docker compose @composeFiles --profile sandbox up -d --build smolvm
} else {
  Write-Info 'skipped (the code-run sandbox needs a Linux host with /dev/kvm)'
}

# ── 6. verify ─────────────────────────────────────────────────────────────────
Write-Section '[6/6] Verifying the stack'
$failed = @()
# ui is a one-shot bundle builder (exits 0 after copying the PWA into the
# ui-dist volume); gateway's depends_on gates on that, so the gateway
# answering below also proves ui completed.
foreach ($svc in 'gateway', 'orchestrator', 'searxng', 'mcp-playwright', 'mcp-scrapling', 'headroom') {
  $cid   = Invoke-Capture docker compose @composeFiles ps -q $svc
  $state = $null
  if ($cid) { $state = Invoke-Capture docker inspect -f '{{.State.Status}}' "$cid" }
  if ("$state" -eq 'running') {
    Write-Info "$svc running"
  } else {
    if (-not $state) { $state = 'not created' }
    $failed += "$svc is $state"
  }
}
# The orchestrator carries the stack's only healthcheck - wait for it.
$orchId = Invoke-Capture docker compose @composeFiles ps -q orchestrator
$health = 'not created'
if ($orchId) {
  foreach ($i in 1..30) {
    $health = "$(Invoke-Capture docker inspect -f '{{.State.Health.Status}}' "$orchId")"
    if ($health -eq 'healthy') { break }
    Start-Sleep -Seconds 3
  }
}
if ($health -eq 'healthy') { Write-Info 'orchestrator healthcheck: healthy' }
else { $failed += "orchestrator healthcheck: $health" }
# End to end: the gateway must answer on :8080.
$gatewayUp = $false
foreach ($i in 1..10) {
  try {
    $resp = Invoke-WebRequest -Uri 'http://localhost:8080/' -UseBasicParsing -TimeoutSec 5
    if ($resp.StatusCode -eq 200) { $gatewayUp = $true; break }
  } catch { Start-Sleep -Seconds 2 }
}
if ($gatewayUp) { Write-Info 'gateway answers on http://localhost:8080' }
else { $failed += 'gateway did not answer HTTP 200 on :8080' }
# Images the orchestrator spawns on demand (sessions + local engine lanes)
# must exist locally or the first session / model load dies at spawn time.
foreach ($img in 'forge-session-runner', 'forge-airllm', 'forge-imagegen') {
  if (Invoke-Capture docker image inspect -f '{{.Id}}' $img) { Write-Info "image $img built" }
  else { $failed += "image $img is missing" }
}
if ($failed.Count -gt 0) {
  docker compose @composeFiles ps
  Stop-Setup ("stack verification failed:`n    " + ($failed -join "`n    "))
}

Write-Section 'Forge is up.'
Write-Host @"
  Open  http://localhost:8080  (or http://<this-machine-ip>:8080 from another
  device on your LAN).
  The first visitor creates the admin profile; everyone else registers there.
  On first boot the model catalog is seeded automatically — open the Models
  page to download weights, then load an engine.

  Handy commands (all via docker compose, no make needed):
    docker compose logs -f --tail=200      # tail logs
    docker compose down                    # stop (volumes kept)
"@
