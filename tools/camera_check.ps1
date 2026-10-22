# Camera check on Windows - start by double-clicking tools\camera_check.bat.
#
# 1. creates/updates the virtual environment .venv with this project
# 2. lists the cameras OpenCV can open
# 3. probes each camera: which resolution / pixel format / frame rate it really delivers,
#    and saves a lossless 4K snapshot (data\raw\probe_4k_usbN.png)
# 4. measures how fast this PC runs the detector at 4K (ota bench)
# Everything is written to data\raw\camera_check.txt.

param([int]$MaxCameras = 3)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
New-Item -ItemType Directory -Force -Path data\raw | Out-Null
$Log = "data\raw\camera_check.txt"
Start-Transcript -Path $Log -Force | Out-Null

function Step($msg) { Write-Host "`n=== $msg ===" -ForegroundColor Cyan }

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
    & $vpy -m pip install --quiet -e ".[openvino]"
    if ($LASTEXITCODE -ne 0) { throw "pip install failed" }

    Step "System"
    Get-CimInstance Win32_Processor | Select-Object Name, NumberOfCores, NumberOfLogicalProcessors |
        Format-Table | Out-String | Write-Host
    Get-PnpDevice -Class Camera, Image -Status OK -ErrorAction SilentlyContinue |
        Select-Object FriendlyName, InstanceId | Format-Table -AutoSize | Out-String | Write-Host

    Step "Cameras"
    $list = & $vpy -m ota cameras --max 6
    $list | Write-Host
    $indices = @($list | Select-String -Pattern '^usb:(\d+)' | ForEach-Object { $_.Matches[0].Groups[1].Value })
    if ($indices.Count -eq 0) { throw "No camera found. Is it plugged in (blue USB 3 port)? Close other camera apps." }

    foreach ($i in $indices | Select-Object -First $MaxCameras) {
        Step "Probe usb:$i (takes about a minute)"
        & $vpy -m ota probe --camera "usb:$i" --fps 30 --frames 30 --snapshot "data\raw\probe_4k_usb$i.png"
    }

    Step "Speed of this PC at 4K"
    & $vpy -m ota bench

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
