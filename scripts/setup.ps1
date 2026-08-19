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

# Run an external command and abort if it exits non-zero.
function Invoke-Checked {
  param([Parameter(ValueFromRemainingArguments = $true)][string[]]$CmdArgs)
  $exe = $CmdArgs[0]
  $rest = @()
  if ($CmdArgs.Length -gt 1) { $rest = $CmdArgs[1..($CmdArgs.Length - 1)] }
  & $exe @rest
  if ($LASTEXITCODE -ne 0) {
    Stop-Setup "command failed: $($CmdArgs -join ' ')"
  }
}

# ── 1. preflight ────────────────────────────────────────────────────────────
Write-Section '[1/5] Checking host prerequisites'
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
  Stop-Setup 'docker not found — install Docker Desktop: https://docs.docker.com/desktop/install/windows-install/'
}
docker info *> $null
if ($LASTEXITCODE -ne 0) {
  Stop-Setup 'the Docker daemon is unreachable — start Docker Desktop and wait for it to report "running", then re-run this script'
}
docker compose version *> $null
if ($LASTEXITCODE -ne 0) {
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
Write-Section '[2/5] Preparing .env'
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
Write-Section '[3/5] Selecting compose files'
$composeFiles = @('-f', 'docker-compose.yml')
if ((-not $NoGpu) -and $hasGpu) {
  $composeFiles += @('-f', 'docker-compose.gpu.yml')
  Write-Info 'including docker-compose.gpu.yml (GPU stats + leases)'
} else {
  Write-Info 'CPU-only bring-up (no GPU overlay)'
}

# ── 4. build + start ────────────────────────────────────────────────────────
Write-Section '[4/5] Building and starting the stack'
Invoke-Checked docker compose @composeFiles up -d --build
# Profile-gated images are invisible to a plain `up` — build them so the
# orchestrator can spawn sessions and the local engine lanes.
Invoke-Checked docker compose @composeFiles --profile build-only build session-runner
Invoke-Checked docker compose @composeFiles --profile engines build airllm imagegen
if (-not $SkipPull) {
  Write-Info 'prefetching the llama.cpp/vLLM engine images (skip next time with -SkipPull)...'
  docker compose @composeFiles --profile engines pull llamacpp vllm
  if ($LASTEXITCODE -ne 0) {
    Write-Info "engine image prefetch skipped/failed — they'll pull on first model load"
  }
}

# ── 5. optional sandbox lane ────────────────────────────────────────────────
Write-Section '[5/5] Optional sandbox lane'
if ($Sandbox) {
  Write-Info 'WARNING: smolvm microVMs need /dev/kvm, which Docker Desktop on Windows does not expose — the sandbox lane will not run here. Use a Linux host for the code-run sandbox.'
  Invoke-Checked docker compose @composeFiles --profile sandbox up -d --build smolvm
} else {
  Write-Info 'skipped (the code-run sandbox needs a Linux host with /dev/kvm)'
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
