# ADS-B test: checks the RTL-SDR driver, starts dump1090 and lists the aircraft received.
# Start by double-clicking tools\adsb_test.bat.
# Expects dump1090.exe somewhere below F:\Programming\dump1090 (or pass -Dump1090 <path to exe>).
param([string]$Dump1090 = "", [int]$Seconds = 90)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
New-Item -ItemType Directory -Force -Path data\raw | Out-Null
Start-Transcript -Path "data\raw\adsb_test.txt" -Force | Out-Null

function Step($msg) { Write-Host "`n=== $msg ===" -ForegroundColor Cyan }
function Run([string]$exe, [string[]]$arguments) {
    $tmp = [System.IO.Path]::GetTempFileName()
    $p = Start-Process -FilePath $exe -ArgumentList $arguments -NoNewWindow -Wait -PassThru `
        -RedirectStandardOutput $tmp -RedirectStandardError "$tmp.err"
    $out = @(Get-Content $tmp) + @(Get-Content "$tmp.err" | Where-Object { $_ -notmatch "WARN|comtypes" })
    $out | ForEach-Object { Write-Host $_ }
    Remove-Item $tmp, "$tmp.err" -ErrorAction SilentlyContinue
}

try {
    Step "RTL-SDR stick and driver"
    $sdr = Get-PnpDevice -PresentOnly -ErrorAction SilentlyContinue |
        Where-Object { $_.InstanceId -match "VID_0BDA&PID_283[28]" }
    if (-not $sdr) { throw "No RTL-SDR stick found (USB VID 0BDA PID 2838). Is it plugged in?" }
    foreach ($d in $sdr) {
        $svc = (Get-PnpDeviceProperty -InstanceId $d.InstanceId -KeyName DEVPKEY_Device_Service `
            -ErrorAction SilentlyContinue).Data
        Write-Host ("{0,-45} driver/service: {1}" -f $d.FriendlyName, $svc)
    }
    if (-not ($sdr | Where-Object { (Get-PnpDeviceProperty -InstanceId $_.InstanceId `
            -KeyName DEVPKEY_Device_Service -ErrorAction SilentlyContinue).Data -match "WinUSB" })) {
        Write-Host "`nThe stick does not use the WinUSB driver yet. Install it with Zadig:" -ForegroundColor Yellow
        Write-Host "  Options > List All Devices > 'Bulk-In, Interface (Interface 0)' > WinUSB > Replace Driver"
    }

    Step "dump1090"
    if (-not $Dump1090) {
        $found = Get-ChildItem -Path "F:\Programming\dump1090" -Recurse -Filter dump1090.exe -ErrorAction SilentlyContinue |
            Select-Object -First 1
        if ($found) { $Dump1090 = $found.FullName }
    }
    if (-not $Dump1090 -or -not (Test-Path $Dump1090)) {
        throw "dump1090.exe not found. Unzip it to F:\Programming\dump1090 (see the instructions)."
    }
    Write-Host "Using $Dump1090"
    $running = Get-Process dump1090 -ErrorAction SilentlyContinue
    if (-not $running) {
        Start-Process -FilePath $Dump1090 -ArgumentList "--net", "--interactive" `
            -WorkingDirectory (Split-Path $Dump1090) | Out-Null
        Write-Host "Started dump1090 in its own window (map: http://localhost:8080). Leave it open."
        Start-Sleep -Seconds 5
    } else { Write-Host "dump1090 is already running." }

    Step "Receiving for $Seconds s"
    $vpy = Join-Path $Root ".venv\Scripts\python.exe"
    if (-not (Test-Path $vpy)) { throw "Run tools\camera_check.bat first (it creates .venv)." }
    & $vpy -m pip install --quiet -e ".[openvino]" pygrabber
    Run $vpy @("-m", "ota", "adsb-check", "--seconds", "$Seconds")
}
catch { Write-Host "`nERROR: $_" -ForegroundColor Red }
finally {
    Stop-Transcript | Out-Null
    Read-Host "`nPress Enter to close"
}
