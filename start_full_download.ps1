param([string]$Python = '', [switch]$Headless)
$ErrorActionPreference = 'Stop'
$processRecord = Join-Path $PSScriptRoot 'background_process.json'
if (Test-Path -LiteralPath $processRecord) {
    $previous = Get-Content -LiteralPath $processRecord -Raw | ConvertFrom-Json
    $existing = Get-Process -Id $previous.process_id -ErrorAction SilentlyContinue
    if ($existing -and $existing.StartTime.ToUniversalTime().Ticks -eq ([datetime]$previous.started_utc).ToUniversalTime().Ticks) {
        Write-Output "Downloader already running: $($existing.Id)"
        exit 0
    }
}
if (-not $Headless) {
    $windowExe = Join-Path $PSScriptRoot 'ERA5-reanalysis-complete下载器v2.0.exe'
    if (-not (Test-Path -LiteralPath $windowExe)) { throw 'Download window executable was not found.' }
    if ($Python) { Set-Content -LiteralPath (Join-Path $PSScriptRoot 'python_path.txt') -Value $Python -Encoding UTF8 }
    Start-Process -FilePath $windowExe -WorkingDirectory $PSScriptRoot -WindowStyle Normal | Out-Null
    Write-Output 'Download window opened. Use pause / resume in the window.'
    exit 0
}
if (-not $Python) {
    $bundledPython = Join-Path $env:USERPROFILE '.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe'
    if (Test-Path -LiteralPath $bundledPython) { $Python = $bundledPython }
    else { $Python = (Get-Command python).Source }
}
$scriptPath = Join-Path $PSScriptRoot 'download_queue.py'
$configPath = Join-Path $PSScriptRoot 'config.json'
$arguments = '-u "{0}" --config "{1}"' -f $scriptPath, $configPath
$taskProcess = Start-Process -FilePath $Python -ArgumentList $arguments -WorkingDirectory $PSScriptRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $PSScriptRoot 'full_download.log') -RedirectStandardError (Join-Path $PSScriptRoot 'full_download.error.log') -PassThru
$record = @{
    process_id = $taskProcess.Id
    started_utc = $taskProcess.StartTime.ToUniversalTime().ToString('o')
    script = $scriptPath
    config = $configPath
    stdout = (Join-Path $PSScriptRoot 'full_download.log')
    stderr = (Join-Path $PSScriptRoot 'full_download.error.log')
}
$record | ConvertTo-Json | Set-Content -LiteralPath $processRecord -Encoding UTF8
Write-Output "Downloader started in background: $($taskProcess.Id)"
