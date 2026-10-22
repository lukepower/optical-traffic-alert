# 30-second test recording with the camera, as the OTA Recorder does it ("record everything"),
# then a check for lost frames. Start by double-clicking tools\record_test.bat.
# Options: -Camera usb:1  -Fps 15  -Seconds 30
param([string]$Camera = "usb:1", [double]$Fps = 15, [int]$Seconds = 30)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
New-Item -ItemType Directory -Force -Path data\raw\sessions | Out-Null
$Log = "data\raw\record_test.txt"
Start-Transcript -Path $Log -Force | Out-Null

function Run([string]$exe, [string[]]$arguments) {
    $tmp = [System.IO.Path]::GetTempFileName()
    $p = Start-Process -FilePath $exe -ArgumentList $arguments -NoNewWindow -Wait -PassThru `
        -RedirectStandardOutput $tmp -RedirectStandardError "$tmp.err"
    $out = @(Get-Content $tmp) + @(Get-Content "$tmp.err" | Where-Object { $_ -notmatch "WARN|comtypes" })
    $out | ForEach-Object { Write-Host $_ }
    Remove-Item $tmp, "$tmp.err" -ErrorAction SilentlyContinue
    return ,$out
}

try {
    $vpy = Join-Path $Root ".venv\Scripts\python.exe"
    if (-not (Test-Path $vpy)) { throw "Run tools\camera_check.bat first (it creates .venv)." }
    & $vpy -m pip install --quiet -e ".[openvino]" pygrabber

    Write-Host "`n=== Recording $Seconds s from $Camera at 3840x2160, $Fps fps ===" -ForegroundColor Cyan
    Write-Host "Point the camera out of the window. Please wait..."
    Run $vpy @("-m", "ota", "session", "--camera", $Camera, "--width", "3840", "--height", "2160",
               "--fps", "$Fps", "--format", "YUY2", "--record-mode", "all", "--adsb", '""',
               "--no-ogn-online", "--station", "TEST", "--out", "data\raw\sessions",
               "--seconds", "$Seconds") | Out-Null

    $sess = Get-ChildItem data\raw\sessions -Directory | Sort-Object Name -Descending | Select-Object -First 1
    Write-Host "`n=== Check ===" -ForegroundColor Cyan
    Run $vpy @("-m", "ota", "session-stats", $sess.FullName) | Out-Null

    Write-Host "`n=== Detection on the recording (no traffic data, just speed and candidates) ===" -ForegroundColor Cyan
    $video = Get-ChildItem (Join-Path $sess.FullName "camera") -Filter *.avi | Select-Object -First 1
    Run $vpy @("-m", "ota", "run", "--video", $video.FullName, "--hfov", "55", "--max-frames", "150",
               "--record", (Join-Path $sess.FullName "overlay.mp4")) | Out-Null
    Write-Host "`nDone. Session folder: $($sess.FullName)" -ForegroundColor Green
}
catch { Write-Host "`nERROR: $_" -ForegroundColor Red }
finally {
    Stop-Transcript | Out-Null
    Read-Host "`nPress Enter to close"
}
