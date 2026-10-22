# Camera check on Windows - start by double-clicking tools\camera_check.bat.
#
# 1. creates/updates the virtual environment .venv with this project
# 2. lists the cameras OpenCV can open
# 3. probes each camera: which resolution / pixel format / frame rate it really delivers,
#    and saves a lossless 4K snapshot (data\raw\probe_4k_usbN.png)
# 4. measures how fast this PC runs the detector at 4K (ota bench)
# Everything is written to data\raw\camera_check.txt.

param([int]$MaxCameras = 4)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
New-Item -ItemType Directory -Force -Path data\raw | Out-Null
$Log = "data\raw\camera_check.txt"
Start-Transcript -Path $Log -Force | Out-Null

function Step($msg) { Write-Host "`n=== $msg ===" -ForegroundColor Cyan }

# Run a program and show its output so that it also ends up in the log
# (Start-Transcript in Windows PowerShell does not record output of .exe programs).
function Run([string]$exe, [string[]]$arguments) {
    $tmp = [System.IO.Path]::GetTempFileName()
    $p = Start-Process -FilePath $exe -ArgumentList $arguments -NoNewWindow -Wait -PassThru `
        -RedirectStandardOutput $tmp -RedirectStandardError "$tmp.err"
    $out = @(Get-Content $tmp) + @(Get-Content "$tmp.err")
    $out | ForEach-Object { Write-Host $_ }
    Remove-Item $tmp, "$tmp.err" -ErrorAction SilentlyContinue
    return ,$out
}

try {
    Step "Python"
    $py = $null
    foreach ($v in @("3.12", "3.11", "3.10")) {
        try { & py "-$v" --version *> $null; if ($LASTEXITCODE -eq 0) { $py = @("py", "-$v"); break } } catch {}
    }
    if (-not $py) {
        try { & python --version; if ($LASTEXITCODE -eq 0) { $py = @("python") } } catch {}
    }
    if (-not $py) { throw "No Python 3.10-3.12 found. Please install it from python.org (Add to PATH)." }

    Step "Virtual environment .venv"
    if (-not (Test-Path ".venv\Scripts\python.exe")) { & $py[0] $py[1..9] -m venv .venv }
    $vpy = Join-Path $Root ".venv\Scripts\python.exe"
    & $vpy -m pip install --quiet --upgrade pip
    & $vpy -m pip install --quiet -e ".[openvino]" pygrabber
    if ($LASTEXITCODE -ne 0) { throw "pip install failed" }

    Step "System"
    Get-CimInstance Win32_Processor | Select-Object Name, NumberOfCores, NumberOfLogicalProcessors |
        Format-Table | Out-String | Write-Host
    $cams = Get-PnpDevice -Class Camera, Image -Status OK -ErrorAction SilentlyContinue
    $cams | Select-Object FriendlyName, Class, InstanceId | Format-Table -AutoSize | Out-String | Write-Host
    foreach ($c in $cams | Where-Object { $_.InstanceId -like "USB*" }) {
        $props = Get-PnpDeviceProperty -InstanceId $c.InstanceId -KeyName `
            DEVPKEY_Device_DriverInfPath, DEVPKEY_Device_DriverProvider, DEVPKEY_Device_Service `
            -ErrorAction SilentlyContinue
        Write-Host ("{0}: driver {1} / {2} / service {3}" -f $c.FriendlyName,
            ($props | Where-Object KeyName -eq DEVPKEY_Device_DriverInfPath).Data,
            ($props | Where-Object KeyName -eq DEVPKEY_Device_DriverProvider).Data,
            ($props | Where-Object KeyName -eq DEVPKEY_Device_Service).Data)
    }

    Step "Cameras"
    $list = Run $vpy @("-m", "ota", "cameras", "--max", "6")
    # skip virtual cameras and webcams that are certainly not the test camera
    $sources = @($list | Where-Object { $_ -match '^(usb|msmf):\d+' -and $_ -notmatch 'BRIO|Eyeware|OBS|Virtual' } |
        ForEach-Object { ($_ -split '\s+')[0] })
    if ($sources.Count -eq 0) { throw "No camera found. Is it plugged in (blue USB 3 port)? Close other camera apps." }

    foreach ($src in $sources | Select-Object -First $MaxCameras) {
        $tag = $src -replace ':', ''
        Step "Probe $src (takes about a minute)"
        Run $vpy @("-m", "ota", "probe", "--camera", $src, "--fps", "30", "--frames", "20",
                   "--snapshot", "data\raw\probe_4k_$tag.png") | Out-Null
    }

    Step "Speed of this PC at 4K"
    Run $vpy @("-m", "ota", "bench") | Out-Null

    Step "Done"
    Write-Host "Results: $Log" -ForegroundColor Green
}
catch {
    Write-Host "`nERROR: $_" -ForegroundColor Red
}
finally {
    Stop-Transcript | Out-Null
    Read-Host "`nPress Enter to close"
}
