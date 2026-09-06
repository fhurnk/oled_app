param(
    [switch]$SkipFrontend,
    [string]$PythonPath
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$python = if ($PythonPath) { (Resolve-Path -LiteralPath $PythonPath).Path } else { Join-Path $projectRoot "env\Scripts\python.exe" }

function Remove-GeneratedDirectory {
    param([string]$Path)

    $fullProjectRoot = [System.IO.Path]::GetFullPath($projectRoot).TrimEnd('\') + '\'
    $fullTarget = [System.IO.Path]::GetFullPath($Path)
    if (-not $fullTarget.StartsWith($fullProjectRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to remove a generated directory outside the project: $fullTarget"
    }
    if (-not (Test-Path -LiteralPath $fullTarget)) {
        return
    }

    Get-ChildItem -LiteralPath $fullTarget -Force -Recurse -ErrorAction SilentlyContinue |
        ForEach-Object {
            $_.Attributes = $_.Attributes -band (-bnot [System.IO.FileAttributes]::ReadOnly)
        }
    $rootItem = Get-Item -LiteralPath $fullTarget -Force
    $rootItem.Attributes = $rootItem.Attributes -band (-bnot [System.IO.FileAttributes]::ReadOnly)
    Remove-Item -LiteralPath $fullTarget -Recurse -Force
}

if (-not (Test-Path -LiteralPath $python)) {
    throw "Project Python environment not found: $python"
}

if (-not $SkipFrontend) {
    & (Join-Path $PSScriptRoot "build_v2_frontend.ps1")
}

$pyinstallerWork = Join-Path $projectRoot "build\oled_v2_alpha"
$pyinstallerDist = Join-Path $projectRoot "dist\OLED Measurement App 2 Alpha"
Remove-GeneratedDirectory $pyinstallerWork
Remove-GeneratedDirectory $pyinstallerDist

Push-Location $projectRoot
try {
    & $python -m PyInstaller --noconfirm --clean "packaging\oled_v2_alpha.spec"
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller failed with exit code $LASTEXITCODE"
    }
}
finally {
    Pop-Location
}

$exe = Join-Path $projectRoot "dist\OLED Measurement App 2 Alpha\OLED Measurement App 2 Alpha.exe"
if (-not (Test-Path -LiteralPath $exe)) {
    throw "PyInstaller did not create $exe"
}
Write-Output "v2 alpha onedir build ready: $exe"
