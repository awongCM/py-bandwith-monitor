# Start eero_monitor serve on Windows with .env loaded (gitignored secrets).
# Usage (from repo root):
#   powershell -ExecutionPolicy Bypass -File deploy\windows\serve.ps1
$ErrorActionPreference = "Stop"

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Set-Location $RepoRoot

$Python = Join-Path $RepoRoot ".venv-eero\Scripts\python.exe"
if (-not (Test-Path $Python)) {
    throw "Missing $Python — create the venv first (see README)."
}

$EnvFile = Join-Path $RepoRoot ".env"
if (Test-Path $EnvFile) {
    Get-Content $EnvFile | ForEach-Object {
        $line = $_.Trim()
        if ($line -eq "" -or $line.StartsWith("#")) { return }
        $eq = $line.IndexOf("=")
        if ($eq -lt 1) { return }
        $name = $line.Substring(0, $eq).Trim()
        $value = $line.Substring($eq + 1).Trim()
        Set-Item -Path "env:$name" -Value $value
    }
}

& $Python -m eero_monitor serve
