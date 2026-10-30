$ErrorActionPreference = 'Stop'
$processRecord = Join-Path $PSScriptRoot 'background_process.json'
if (-not (Test-Path -LiteralPath $processRecord)) { Write-Output 'No background process record.'; exit 0 }
$record = Get-Content -LiteralPath $processRecord -Raw | ConvertFrom-Json
$taskProcess = Get-Process -Id $record.process_id -ErrorAction SilentlyContinue
if ($taskProcess -and $taskProcess.StartTime.ToUniversalTime().Ticks -eq ([datetime]$record.started_utc).ToUniversalTime().Ticks) {
    Stop-Process -Id $taskProcess.Id
    Write-Output 'Downloader stopped. Completed hourly files retained; restart to resume.'
} else { Write-Output 'The recorded downloader is no longer running.' }
