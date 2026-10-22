# Build dump1090.exe for Windows from the gvanem/Dump1090 source (MIT licence).
# Start by double-clicking tools\build_dump1090.bat.
#
# 1. finds the unpacked source below F:\Programming\dump1090 (Code > Download ZIP from
#    https://github.com/gvanem/Dump1090, unzip there)
# 2. finds Visual Studio / Build Tools with the C++ compiler and CMake; if missing, offers to
#    install "Build Tools for Visual Studio 2022" (C++ workload) with winget
# 3. builds Release x64 with CMake; dump1090.exe ends up in the source folder
# 4. adapts dump1090.cfg next to it (home position, small web map, no aircraft database
#    download; the original is kept as dump1090.cfg.orig)
param([string]$Source = "F:\Programming\dump1090", [string]$HomePos = "46.4603,11.3264")

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
New-Item -ItemType Directory -Force -Path (Join-Path $Root "data\raw") | Out-Null
Start-Transcript -Path (Join-Path $Root "data\raw\build_dump1090.txt") -Force | Out-Null
function Step($msg) { Write-Host "`n=== $msg ===" -ForegroundColor Cyan }

function Find-VS {
    $vswhere = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
    if (-not (Test-Path $vswhere)) { return $null }
    $path = & $vswhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 `
        -property installationPath
    if ($path) { return $path.Trim() } else { return $null }
}

try {
    Step "Source"
    $cml = Get-ChildItem -Path $Source -Recurse -Filter CMakeLists.txt -ErrorAction SilentlyContinue |
        Where-Object { $_.Directory.Name -eq "src" -and (Test-Path (Join-Path $_.Directory.FullName "dump1090.c")) } |
        Select-Object -First 1
    if (-not $cml) {
        throw "No dump1090 source found below $Source. Download https://github.com/gvanem/Dump1090 (Code > Download ZIP) and unzip it there."
    }
    $src = $cml.Directory.FullName
    $top = Split-Path $src
    Write-Host "Source: $top"

    Step "Visual Studio C++ compiler and CMake"
    $vs = Find-VS
    if (-not $vs) {
        Write-Host "No Visual Studio C++ compiler found." -ForegroundColor Yellow
        $ans = Read-Host "Install 'Build Tools for Visual Studio 2022' (C++, ~3 GB, needs admin rights) with winget now? [y/N]"
        if ($ans -notmatch '^[yYjJ]') { throw "Please install Build Tools for Visual Studio 2022 with the 'Desktop development with C++' workload, then run this again." }
        winget install --id Microsoft.VisualStudio.2022.BuildTools -e --accept-package-agreements `
            --accept-source-agreements --override "--wait --passive --add Microsoft.VisualStudio.Workload.VCTools --includeRecommended"
        $vs = Find-VS
        if (-not $vs) { throw "Build Tools installation not found after winget. Restart the PC and run this again." }
    }
    Write-Host "Visual Studio: $vs"
    $cmake = Join-Path $vs "Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin\cmake.exe"
    if (-not (Test-Path $cmake)) {
        $cmd = Get-Command cmake -ErrorAction SilentlyContinue
        if ($cmd) { $cmake = $cmd.Source } else {
            throw "CMake not found. In the Visual Studio Installer add 'C++ CMake tools for Windows', or: winget install Kitware.CMake"
        }
    }
    Write-Host "CMake: $cmake"

    Step "Building (Release, x64)"
    $build = Join-Path $top "build-ota"
    & $cmake -S $src -B $build -A x64
    if ($LASTEXITCODE -ne 0) { throw "CMake configure failed" }
    & $cmake --build $build --config Release
    if ($LASTEXITCODE -ne 0) { throw "Build failed" }
    $exe = Get-ChildItem -Path $top -Recurse -Filter dump1090.exe | Sort-Object LastWriteTime -Descending |
        Select-Object -First 1
    if (-not $exe) { throw "dump1090.exe not found after the build" }
    Write-Host "Built: $($exe.FullName)" -ForegroundColor Green

    Step "Configuration"
    $cfgIn = Join-Path $top "dump1090.cfg"
    if (Test-Path $cfgIn) {
        if (-not (Test-Path "$cfgIn.orig")) { Copy-Item $cfgIn "$cfgIn.orig" }
        $cfgOut = Join-Path $exe.DirectoryName "dump1090.cfg"
        $lines = Get-Content "$cfgIn.orig"
        $lines = $lines -replace '^homepos\s*=.*', "homepos = $HomePos" `
                        -replace '^aircrafts\s*=.*', 'aircrafts = NUL'
        if (Test-Path (Join-Path $exe.DirectoryName "web_root\gmap.html")) {
            $lines = $lines -replace '^web-page\s*=.*', 'web-page = %~dp0\web_root\gmap.html'
        }
        Set-Content -Path $cfgOut -Value $lines
        Write-Host "Wrote $cfgOut (home position $HomePos, no aircraft database download)"
    }
    Write-Host "`nDone. Next: tools\adsb_test.bat" -ForegroundColor Green
}
catch { Write-Host "`nERROR: $_" -ForegroundColor Red }
finally {
    Stop-Transcript | Out-Null
    Read-Host "`nPress Enter to close"
}
