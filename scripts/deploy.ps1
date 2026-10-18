# Deploy backend (Railway) + dashboard (Vercel). Never prints secret values.
# Prereqs: railway login, vercel login; .env has DATABASE_URL (+ optional keys).
param(
  [string]$ProjectName = "4ward"
)
$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root

function Require-Cmd($name) {
  if (-not (Get-Command $name -ErrorAction SilentlyContinue)) { throw "Missing CLI: $name" }
}
Require-Cmd railway
Require-Cmd vercel

railway whoami | Out-Null
vercel whoami | Out-Null

# Load .env into process (no print)
$envMap = @{}
Get-Content "$root\.env" | ForEach-Object {
  if ($_ -match '^\s*#' -or $_ -notmatch '=') { return }
  $k, $v = $_.Split('=', 2)
  $k = $k.Trim(); $v = $v.Trim().Trim('"').Trim("'")
  if ($k) { $envMap[$k] = $v; Set-Item -Path "Env:$k" -Value $v }
}

# Shared bot token: reuse existing or generate once
$token = $envMap["BOT_TOKEN"]
if (-not $token) {
  $token = [guid]::NewGuid().ToString("N") + [guid]::NewGuid().ToString("N").Substring(0, 16)
  Add-Content "$root\.env" "`nBOT_TOKEN=$token"
  Add-Content "$root\.env" "NEXT_PUBLIC_BOT_TOKEN=$token"
  $env:BOT_TOKEN = $token
  $env:NEXT_PUBLIC_BOT_TOKEN = $token
  Write-Host "Generated BOT_TOKEN and wrote to .env (not printed)."
}

Write-Host "=== Railway: create/link + deploy backend ==="
if (-not (Test-Path "$root\.railway")) {
  railway init --name $ProjectName -y 2>&1 | Out-Host
}
railway up -y --detach 2>&1 | Out-Host
railway domain 2>&1 | Out-Host

# Set Railway env vars without echoing values
$railwayKeys = @(
  "DATABASE_URL", "GEMINI_API_KEY", "GEMINI_MODEL", "STEEL_API_KEY",
  "BOT_TOKEN", "BROWSER_BACKEND", "REPLAY", "INGEST_EVERY_S", "RATE_LIMIT_PER_MIN"
)
foreach ($k in $railwayKeys) {
  $v = [Environment]::GetEnvironmentVariable($k)
  if ($v) {
    railway variables set "$k=$v" --skip-deploys 2>&1 | Out-Null
    Write-Host "Railway set $k (present)"
  } else {
    Write-Host "Railway skip $k (missing locally)"
  }
}

$backendUrl = (railway domain 2>&1 | Select-String -Pattern 'https://\S+' | Select-Object -First 1).Matches.Value
if (-not $backendUrl) {
  # fallback: railway status / open
  $backendUrl = railway variables --kv 2>$null | Out-Null
  $status = railway status 2>&1 | Out-String
  if ($status -match 'https://[\w.-]+\.up\.railway\.app') { $backendUrl = $Matches[0] }
}
if (-not $backendUrl) { throw "Could not determine Railway public URL. Run: railway domain" }
Write-Host "BACKEND_URL=$backendUrl"

Write-Host "=== Vercel: deploy dashboard ==="
Set-Location "$root\dashboard"
$env:NEXT_PUBLIC_API_URL = $backendUrl
$env:NEXT_PUBLIC_BOT_TOKEN = $token
# Pass build env for this deploy
vercel pull --yes --environment=production 2>&1 | Out-Host
vercel env add NEXT_PUBLIC_API_URL production 2>&1 | Out-Null
# Use vercel env via printf pipe where needed — set with vercel CLI non-interactive:
@"
$backendUrl
"@ | vercel env add NEXT_PUBLIC_API_URL production --force 2>&1 | Out-Null
@"
$token
"@ | vercel env add NEXT_PUBLIC_BOT_TOKEN production --force 2>&1 | Out-Null

$deployOut = vercel --prod --yes 2>&1 | Out-String
$deployOut | Out-Host
$dashUrl = ($deployOut | Select-String -Pattern 'https://[\w.-]+\.vercel\.app' -AllMatches).Matches.Value | Select-Object -Last 1
if (-not $dashUrl) { throw "Could not parse Vercel URL from deploy output" }
Write-Host "DASHBOARD_URL=$dashUrl"

Set-Location $root
# Point Railway CORS at the dashboard
railway variables set "CORS_ORIGINS=$dashUrl,http://localhost:3000,http://127.0.0.1:3000" 2>&1 | Out-Null
Write-Host "Railway CORS_ORIGINS updated (includes Vercel + localhost)"

Write-Host "=== Connectivity check ==="
try {
  $h = Invoke-RestMethod -Uri "$backendUrl/health" -TimeoutSec 30
  Write-Host ("BACKEND_HEALTH status=" + $h.status + " bot_token_required=" + $h.bot_token_required + " backend_db=" + $h.backend_db)
} catch {
  Write-Host "BACKEND_HEALTH failed: $($_.Exception.Message)"
}
try {
  $live = Invoke-WebRequest -Uri "$backendUrl/live" -Headers @{ Origin = $dashUrl } -TimeoutSec 60
  Write-Host "LIVE_FROM_ORIGIN status=$($live.StatusCode) cors=$($live.Headers['Access-Control-Allow-Origin'])"
} catch {
  Write-Host "LIVE_FROM_ORIGIN check: $($_.Exception.Message)"
}

Write-Host ""
Write-Host "DONE"
Write-Host "Backend:   $backendUrl"
Write-Host "Dashboard: $dashUrl"
