# Build, arranque, logs, tests, evaluación y actualización del stack de producto.
param(
    [Parameter(Position = 0)]
    [ValidateSet("build", "up", "down", "logs", "test", "eval", "update", "backup", "drill")]
    [string]$Command = "up"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
$EnvFile = Join-Path $Root ".env.product"
$Compose = @("--env-file", $EnvFile, "-f", "infra/compose.yml")

function New-HexSecret {
    $bytes = New-Object byte[] 32
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $rng.GetBytes($bytes)
    }
    finally {
        $rng.Dispose()
    }
    return ([System.BitConverter]::ToString($bytes)).Replace("-", "").ToLowerInvariant()
}

function Ensure-ResticPassword {
    $path = Join-Path $Root ".restic-password"
    if (Test-Path -LiteralPath $path) {
        return
    }
    $utf8 = New-Object System.Text.UTF8Encoding $false
    [System.IO.File]::WriteAllText($path, (New-HexSecret) + "`n", $utf8)
}

function Ensure-ProductEnv {
    if (Test-Path -LiteralPath $EnvFile) {
        return
    }
    $password = New-HexSecret
    $jwt = New-HexSecret
    $pseudonym = New-HexSecret
    $content = @"
RAG_ENVIRONMENT=production
RAG_LOG_LEVEL=INFO
RAG_POSTGRES_USER=engine_rag
RAG_POSTGRES_PASSWORD=$password
RAG_POSTGRES_DB=engine_rag
RAG_JWT_SECRET=$jwt
RAG_JWT_ISSUER=engine-rag
RAG_JWT_AUDIENCE=engine-rag-api
RAG_PSEUDONYM_SECRET=$pseudonym
RAG_CORS_ORIGINS=http://127.0.0.1:8080,http://localhost:8080
RAG_PUBLIC_HTTPS=false
RAG_REFRESH_COOKIE_SECURE=true
RAG_OLLAMA_BASE_URL=http://host.docker.internal:11434
RAG_GATEWAY_PORT=8080
ENGINE_RAG_VERSION=0.1.0
"@
    $utf8 = New-Object System.Text.UTF8Encoding $false
    [System.IO.File]::WriteAllText($EnvFile, ($content -replace "`r`n", "`n") + "`n", $utf8)
}

function Set-Revision {
    $revision = "dev"
    try {
        $parsed = (& git rev-parse --short HEAD 2>$null)
        if ($LASTEXITCODE -eq 0 -and $parsed) {
            $revision = $parsed.Trim()
        }
    }
    catch {
        $revision = "dev"
    }
    $env:ENGINE_RAG_REVISION = $revision
}

function Invoke-Compose {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$ComposeArgs)
    & docker compose @Compose @ComposeArgs
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
}

switch ($Command) {
    "build" {
        Ensure-ProductEnv
        Ensure-ResticPassword
        Set-Revision
        Invoke-Compose build
    }
    "up" {
        Ensure-ProductEnv
        Ensure-ResticPassword
        Set-Revision
        Invoke-Compose up -d --build --wait --wait-timeout 900
    }
    "down" {
        Ensure-ProductEnv
        Ensure-ResticPassword
        Invoke-Compose down
    }
    "logs" {
        Ensure-ProductEnv
        Ensure-ResticPassword
        Invoke-Compose logs -f --tail 200
    }
    "test" {
        & uv run ruff check .
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
        & uv run ruff format --check .
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
        & uv run --project backend mypy backend/app
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
        & uv run --project backend pytest
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    }
    "eval" {
        Ensure-ProductEnv
        Ensure-ResticPassword
        Invoke-Compose run --rm --no-deps --entrypoint python api /app/deterministic_gate.py
    }
    "update" {
        Ensure-ProductEnv
        Ensure-ResticPassword
        Set-Revision
        Invoke-Compose build
        Invoke-Compose up -d --wait --wait-timeout 900 --force-recreate
    }
    "backup" {
        Ensure-ProductEnv
        Ensure-ResticPassword
        Set-Revision
        Invoke-Compose --profile backup run --rm backup
    }
    "drill" {
        & uv run --project backend python -m app.cli.drill
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    }
}
