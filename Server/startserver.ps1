param(
    [int]$Port = 5000,
    [string]$BindHost = "127.0.0.1"
)

$ErrorActionPreference = "Stop"
$ServerDirectory = $PSScriptRoot
$Python = Join-Path $ServerDirectory ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $Python)) {
    throw "Project Python environment not found at '$Python'. Create it first with 'uv sync' from the Server directory."
}

Push-Location $ServerDirectory
try {
    & $Python -m uvicorn server.main:app --app-dir src --host $BindHost --port $Port
}
finally {
    Pop-Location
}
