# Train the crop classifier on a Windows PC with an NVIDIA or AMD Radeon GPU.
#
# Start by double-clicking tools\train_windows.bat (or run this script in PowerShell).
# What it does:
#   1. creates a separate virtual environment .venv-train (does not touch .venv)
#   2. installs PyTorch for the detected GPU:
#        NVIDIA -> CUDA build from download.pytorch.org
#        AMD    -> ROCm for Windows build from repo.radeon.com (Python 3.12 and
#                  AMD Software: Adrenalin Edition 26.2.2 or newer required;
#                  supported: RX 9000 / RX 7000 series, see docs/training.md)
#      plus Ultralytics, OpenVINO and this project
#   3. checks that the GPU is visible (falls back to CPU training with -AllowCpu)
#   4. waits until the training crops are complete (data\crops\READY)
#   5. trains tools\train_classifier.py and exports models\classifier.xml/.bin
# Everything is logged to data\train_windows.log.

param(
    [int]$Epochs = 40,
    [string]$Data = "data\crops",
    [string]$CudaIndex = "https://download.pytorch.org/whl/cu126",
    [string]$RocmRepo = "https://repo.radeon.com/rocm/windows/rocm-rel-7.2.1",
    [ValidateSet("auto", "nvidia", "amd", "cpu")] [string]$Gpu = "auto",
    [switch]$AllowCpu
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
New-Item -ItemType Directory -Force -Path data | Out-Null
Start-Transcript -Path "data\train_windows.log" -Append | Out-Null

function Step($msg) { Write-Host "`n=== $msg ===" -ForegroundColor Cyan }

try {
    Step "Looking for Python"
    $py = $null
    foreach ($v in @("3.12", "3.11", "3.10")) {   # 3.12 first: required for ROCm wheels
        try { & py "-$v" --version *> $null; if ($LASTEXITCODE -eq 0) { $py = @("py", "-$v"); break } } catch {}
    }
    if (-not $py) {
        try { & python --version; if ($LASTEXITCODE -eq 0) { $py = @("python") } } catch {}
    }
    if (-not $py) { throw "No Python 3.10-3.12 found. Please install it from python.org (Add to PATH)." }
    Write-Host "Python: $($py -join ' ')"

    Step "Virtual environment .venv-train"
    if (-not (Test-Path ".venv-train\Scripts\python.exe")) {
        & $py[0] $py[1..9] -m venv .venv-train
    }
    $vpy = Join-Path $Root ".venv-train\Scripts\python.exe"
    & $vpy -m pip install --upgrade pip

    Step "Detecting the GPU"
    if ($Gpu -eq "auto") {
        $cards = Get-CimInstance Win32_VideoController | Select-Object Name, DriverVersion
        $cards | Format-Table | Out-String | Write-Host
        if ($cards.Name -match "NVIDIA") { $Gpu = "nvidia" }
        elseif ($cards.Name -match "Radeon RX (7|9)\d{3}") { $Gpu = "amd" }
        else { $Gpu = "cpu" }
    }
    Write-Host "Backend: $Gpu"

    $torchVer = & $vpy -c "import torch; print(torch.__version__)" 2>$null
    if ($Gpu -eq "nvidia") {
        Step "Installing PyTorch with CUDA (about 3 GB, takes a while)"
        if ($torchVer -and $torchVer -notmatch "\+cu") { & $vpy -m pip uninstall -y torch torchvision torchaudio }
        & $vpy -m pip install torch torchvision --index-url $CudaIndex
        if ($LASTEXITCODE -ne 0) { throw "PyTorch installation failed" }
    }
    elseif ($Gpu -eq "amd") {
        Step "Installing PyTorch with ROCm for Windows (about 3 GB, takes a while)"
        if ($py[1] -ne "-3.12") { throw "The ROCm wheels need Python 3.12 (py -3.12). Please install it from python.org." }
        if ($torchVer -and $torchVer -notmatch "rocm") { & $vpy -m pip uninstall -y torch torchvision torchaudio }
        & $vpy -m pip install --no-cache-dir "$RocmRepo/rocm_sdk_core-7.2.1-py3-none-win_amd64.whl" "$RocmRepo/rocm_sdk_devel-7.2.1-py3-none-win_amd64.whl" "$RocmRepo/rocm_sdk_libraries_custom-7.2.1-py3-none-win_amd64.whl" "$RocmRepo/rocm-7.2.1.tar.gz"
        if ($LASTEXITCODE -ne 0) { throw "ROCm SDK installation failed" }
        & $vpy -m pip install --no-cache-dir "$RocmRepo/torch-2.9.1%2Brocm7.2.1-cp312-cp312-win_amd64.whl" "$RocmRepo/torchvision-0.24.1%2Brocm7.2.1-cp312-cp312-win_amd64.whl"
        if ($LASTEXITCODE -ne 0) { throw "PyTorch (ROCm) installation failed" }
    }
    else {
        Step "Installing PyTorch (CPU only)"
        & $vpy -m pip install torch torchvision
    }

    Step "Installing Ultralytics, OpenVINO and the project"
    & $vpy -m pip install -e ".[train,openvino]"
    if ($LASTEXITCODE -ne 0) { throw "Installation failed" }

    Step "Checking the GPU"
    $device = "0"
    & $vpy -c "import torch; ok=torch.cuda.is_available(); print('torch', torch.__version__, 'GPU available:', ok); print('GPU:', torch.cuda.get_device_name(0) if ok else '-'); raise SystemExit(0 if ok else 1)"
    if ($LASTEXITCODE -ne 0) {
        if ($Gpu -eq "amd") { Write-Host "Hint: AMD Software: Adrenalin Edition 26.2.2 or newer is required for ROCm on Windows." -ForegroundColor Yellow }
        if (-not $AllowCpu -and $Gpu -ne "cpu") { throw "No usable GPU. Update the graphics driver, or start with -AllowCpu to train on the CPU (slower)." }
        Write-Host "Training on the CPU." -ForegroundColor Yellow
        $device = "cpu"
    }

    Step "Waiting for training data ($Data\READY)"
    while (-not (Test-Path "$Data\READY")) {
        $n = (Get-ChildItem -Recurse -Filter *.png "$Data" -ErrorAction SilentlyContinue | Measure-Object).Count
        Write-Host ("{0:HH:mm:ss}  {1} crops so far - waiting..." -f (Get-Date), $n)
        Start-Sleep -Seconds 30
    }

    Step "Training ($Epochs epochs)"
    & $vpy tools\train_classifier.py --data $Data --epochs $Epochs --device $device
    if ($LASTEXITCODE -ne 0) { throw "Training failed" }

    Step "Done"
    Get-ChildItem models\classifier.* | Format-Table Name, Length, LastWriteTime
    New-Item -ItemType File -Force -Path "data\TRAINING_DONE" | Out-Null
}
catch {
    Write-Host "`nERROR: $_" -ForegroundColor Red
    New-Item -ItemType File -Force -Path "data\TRAINING_FAILED" | Out-Null
}
finally {
    Stop-Transcript | Out-Null
    Read-Host "`nPress Enter to close"
}
