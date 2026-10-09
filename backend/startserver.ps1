param(
    [int]$Port = 5000,
    [string]$BindHost = "127.0.0.1"
)

$ErrorActionPreference = "Stop"
$backendDirectory = $PSScriptRoot
$Python = Join-Path $backendDirectory ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $Python)) {
    throw "Project Python environment not found at '$Python'. Create it first with 'uv sync' from the backend directory."
}

Push-Location $backendDirectory
try {
    & $Python -m uvicorn server.main:app --app-dir src --host $BindHost --port $Port
}
finally {
    Pop-Location
}
