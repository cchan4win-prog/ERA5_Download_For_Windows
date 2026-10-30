$ErrorActionPreference = 'Stop'
$compiler = Join-Path $env:WINDIR 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
Push-Location -LiteralPath $PSScriptRoot
try {
    & $compiler /nologo /target:winexe /codepage:65001 '/out:ERA5-reanalysis-complete下载器v2.0.exe' /reference:System.Windows.Forms.dll /reference:System.Drawing.dll /reference:System.Web.Extensions.dll DownloaderWindow.cs ConfigurationWindow.cs
    if ($LASTEXITCODE -ne 0) { throw 'Window compilation failed.' }
} finally { Pop-Location }
