# GPS receiver test: lists the COM ports and reads the GPS for 90 s (fix, satellites, rate).
# Start by double-clicking tools\gps_test.bat. Option: -Port COM5@9600
param([string]$Port = "auto", [int]$Seconds = 90)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
New-Item -ItemType Directory -Force -Path data\raw | Out-Null
Start-Transcript -Path "data\raw\gps_test.txt" -Force | Out-Null

function Run([string]$exe, [string[]]$arguments) {
    $tmp = [System.IO.Path]::GetTempFileName()
    $p = Start-Process -FilePath $exe -ArgumentList $arguments -NoNewWindow -Wait -PassThru `
        -RedirectStandardOutput $tmp -RedirectStandardError "$tmp.err"
    $out = @(Get-Content $tmp) + @(Get-Content "$tmp.err" | Where-Object { $_ -notmatch "WARN|comtypes" })
    $out | ForEach-Object { Write-Host $_ }
    Remove-Item $tmp, "$tmp.err" -ErrorAction SilentlyContinue
}

try {
    $vpy = Join-Path $Root ".venv\Scripts\python.exe"
    if (-not (Test-Path $vpy)) { throw "Run tools\camera_check.bat first (it creates .venv)." }
    & $vpy -m pip install --quiet -e ".[openvino,serial]" pygrabber
    Write-Host "`n=== COM ports ===" -ForegroundColor Cyan
    Run $vpy @("-m", "ota", "ports")
    Write-Host "`n=== GPS ===" -ForegroundColor Cyan
    Run $vpy @("-m", "ota", "gps-check", "--port", $Port, "--seconds", "$Seconds")
}
catch { Write-Host "`nERROR: $_" -ForegroundColor Red }
finally {
    Stop-Transcript | Out-Null
    Read-Host "`nPress Enter to close"
}
