param(
    [string]$Python = "",
    [int]$MaxHours = 0,
    [int]$TimeBudget = 0,
    [switch]$Plan
)
$ErrorActionPreference = 'Stop'
if (-not $Python) {
    $bundledPython = Join-Path $env:USERPROFILE '.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe'
    if (Test-Path -LiteralPath $bundledPython) { $Python = $bundledPython }
    else { $Python = 'python' }
}
if ($MaxHours -gt 0 -or $TimeBudget -gt 0) { throw 'Limited-hour trials require download_google_era5.py with a single-task configuration. This launcher runs the V2 queue.' }
$arguments = @('-u', (Join-Path $PSScriptRoot 'download_queue.py'), '--config', (Join-Path $PSScriptRoot 'config.json'))
if ($Plan) { $arguments += '--plan' }
& $Python @arguments
if ($LASTEXITCODE -ne 0) { throw "Download failed (exit $LASTEXITCODE). Completed hourly files are retained." }
