param(
    [switch]$Install
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$frontendRoot = Join-Path $projectRoot "v2_frontend"

if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
    throw "Node.js 20.19+ is required to build the frontend. It is not required to run the packaged application."
}
if (-not (Get-Command pnpm -ErrorAction SilentlyContinue)) {
    throw "pnpm is required to build the frontend. It is not required to run the packaged application."
}

Push-Location $frontendRoot
try {
    $env:CI = "true"
    if ($Install) {
        if (Test-Path -LiteralPath "pnpm-lock.yaml") {
            pnpm install --frozen-lockfile
        }
        else {
            pnpm install
        }
        if ($LASTEXITCODE -ne 0) {
            throw "pnpm install failed with exit code $LASTEXITCODE"
        }
    }
    pnpm run build
    if ($LASTEXITCODE -ne 0) {
        throw "Frontend build failed with exit code $LASTEXITCODE"
    }
}
finally {
    Pop-Location
}

$indexPath = Join-Path $projectRoot "oled_v2\static\index.html"
if (-not (Test-Path -LiteralPath $indexPath)) {
    throw "Vite did not create $indexPath"
}
Write-Output "Frontend ready: $indexPath"
