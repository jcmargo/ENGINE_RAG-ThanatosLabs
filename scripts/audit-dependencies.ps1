# Revisa dependencias de backend y frontend. Informa; no cambia lockfiles.
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

Write-Host "pip-audit"
uv run --project backend --group dev pip-audit
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

Write-Host "npm audit"
npm --prefix frontend audit
exit $LASTEXITCODE
