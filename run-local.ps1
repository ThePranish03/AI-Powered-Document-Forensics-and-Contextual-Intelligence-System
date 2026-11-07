$ErrorActionPreference = "Stop"

$projectRoot = $PSScriptRoot
$backendDir = Join-Path $projectRoot "backend"
$frontendDir = Join-Path $projectRoot "frontend\DocForensIQ-frontend"
$ocrDir = Join-Path $projectRoot "..\extra_things\PaddleOcr\ocr_evaluation"
$backendEnv = Join-Path $backendDir ".env"
$frontendEnv = Join-Path $frontendDir ".env.local"
$logDir = Join-Path $projectRoot "logs"

$uvCommand = Get-Command uv -ErrorAction SilentlyContinue
$nodeCommand = Get-Command node -ErrorAction SilentlyContinue
if (-not $uvCommand) { throw "uv is required. Install it first, then run this script again." }
if (-not $nodeCommand) { throw "Node.js is required. Install it first, then run this script again." }
if (-not (Test-Path -LiteralPath $ocrDir)) { throw "PaddleOCR project not found at $ocrDir" }
$occupiedPorts = @(8000, 5173) | ForEach-Object { Get-NetTCPConnection -State Listen -LocalPort $_ -ErrorAction SilentlyContinue }
if ($occupiedPorts) { throw "Ports 8000/5173 are already in use. Do not start a second local stack; use 'docker compose up -d --build' from this directory so the OCR worker shares the same database and file storage." }

if (-not (Test-Path -LiteralPath $backendEnv)) {
    Copy-Item -LiteralPath (Join-Path $backendDir ".env.example") -Destination $backendEnv
    Write-Host "Created backend\.env from its example. Add your xAI key there to enable relationship extraction."
}
if (-not (Test-Path -LiteralPath $frontendEnv)) {
    Copy-Item -LiteralPath (Join-Path $frontendDir ".env.example") -Destination $frontendEnv
}

$ocrPython = $env:PADDLEOCR_PYTHON
if (-not $ocrPython) {
    $ocrSetting = Get-Content -LiteralPath $backendEnv | Where-Object { $_ -match '^\s*PADDLEOCR_PYTHON=' } | Select-Object -Last 1
    if ($ocrSetting) { $ocrPython = ($ocrSetting -replace '^\s*PADDLEOCR_PYTHON=', '').Trim().Trim('"').Trim("'") }
}
if (-not $ocrPython) { $ocrPython = Join-Path $ocrDir ".venv\Scripts\python.exe" }
if (-not [System.IO.Path]::IsPathRooted($ocrPython)) { $ocrPython = [System.IO.Path]::GetFullPath((Join-Path $backendDir $ocrPython)) }
if (-not (Test-Path -LiteralPath $ocrPython)) {
    throw "PaddleOCR runtime is missing at '$ocrPython'. Create the isolated PaddleOCR environment described in extra_things\PaddleOcr\ocr_evaluation\README.md, then rerun this command."
}

Write-Host "Syncing backend dependencies..."
Push-Location $backendDir
try {
    & $uvCommand.Source sync --extra test --locked
    if ($LASTEXITCODE -ne 0) { throw "Backend dependency setup failed." }
    & $uvCommand.Source run alembic upgrade head
    if ($LASTEXITCODE -ne 0) { throw "Database migrations failed." }
} finally {
    Pop-Location
}

if (-not (Test-Path -LiteralPath (Join-Path $frontendDir "node_modules"))) {
    Write-Host "Installing frontend dependencies..."
    Push-Location $frontendDir
    try {
        & npm.cmd ci
        if ($LASTEXITCODE -ne 0) { throw "Frontend dependency setup failed." }
    } finally {
        Pop-Location
    }
}

Write-Host "Checking the isolated PaddleOCR runner..."
Push-Location $ocrDir
try {
    & $ocrPython -m ocr_evaluation.main --help | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "PaddleOCR environment is missing the evaluation runner or its dependencies." }
} finally {
    Pop-Location
}

New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$pythonPath = Join-Path $backendDir ".venv\Scripts\python.exe"
$vitePath = Join-Path $frontendDir "node_modules\vite\bin\vite.js"
$processes = [System.Collections.Generic.List[System.Diagnostics.Process]]::new()

try {
    $processes.Add((Start-Process -FilePath $pythonPath -ArgumentList @("-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000") -WorkingDirectory $backendDir -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $logDir "api.out.log") -RedirectStandardError (Join-Path $logDir "api.err.log")))
    $processes.Add((Start-Process -FilePath $pythonPath -ArgumentList @("-m", "scripts.run_ocr_worker", "--watch") -WorkingDirectory $backendDir -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $logDir "ocr-worker.out.log") -RedirectStandardError (Join-Path $logDir "ocr-worker.err.log")))
    $processes.Add((Start-Process -FilePath $nodeCommand.Source -ArgumentList @($vitePath, "--host", "127.0.0.1") -WorkingDirectory $frontendDir -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $logDir "frontend.out.log") -RedirectStandardError (Join-Path $logDir "frontend.err.log")))

    Start-Sleep -Seconds 2
    foreach ($process in $processes) {
        if ($process.HasExited) { throw "A service exited during startup. Review logs\ for details." }
    }
    Write-Host "DocForensIQ is running at http://localhost:5173"
    Write-Host "API docs: http://localhost:8000/docs"
    Write-Host "OCR worker is running. Logs are in $logDir"
    Write-Host "Press Ctrl+C to stop all three services."
    Start-Process "http://localhost:5173"

    while ($true) {
        Start-Sleep -Seconds 2
        foreach ($process in $processes) {
            $process.Refresh()
            if ($process.HasExited) { throw "A service stopped. Review logs\ for details." }
        }
    }
} finally {
    foreach ($process in $processes) {
        $process.Refresh()
        if (-not $process.HasExited) {
            & taskkill.exe /PID $process.Id /T /F | Out-Null
        }
    }
}
