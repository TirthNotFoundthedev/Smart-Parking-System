param(
    [string]$ReportPath = "result.html",
    [switch]$OpenReport
)

$ErrorActionPreference = "Stop"
$ServerDirectory = $PSScriptRoot
$Python = Join-Path $ServerDirectory ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $Python)) {
    throw "Project Python environment not found at '$Python'. Create it first with 'uv sync' from the Server directory."
}

if ([System.IO.Path]::IsPathRooted($ReportPath)) {
    $ResolvedReportPath = $ReportPath
}
else {
    $ResolvedReportPath = Join-Path $ServerDirectory $ReportPath
}

Push-Location $ServerDirectory
try {
    & $Python (Join-Path $ServerDirectory "run_tests.py") --report $ResolvedReportPath
    $TestExitCode = $LASTEXITCODE
}
finally {
    Pop-Location
}

if (Test-Path -LiteralPath $ResolvedReportPath) {
    Write-Host "Open the report: $ResolvedReportPath"
    if ($OpenReport) {
        Start-Process -FilePath $ResolvedReportPath
    }
}

exit $TestExitCode
