# Train the crop classifier on a Windows PC with an NVIDIA GPU.
#
# Start by double-clicking tools\train_windows.bat (or run this script in PowerShell).
# What it does:
#   1. creates a separate virtual environment .venv-train (does not touch .venv)
#   2. installs PyTorch with CUDA, Ultralytics, OpenVINO and this project
#   3. checks that the GPU is visible
#   4. waits until the training crops are complete (data\crops\READY)
#   5. trains tools\train_classifier.py and exports models\classifier.xml/.bin
# Everything is logged to data\train_windows.log.

param(
    [int]$Epochs = 40,
    [string]$Data = "data\crops",
    [string]$CudaIndex = "https://download.pytorch.org/whl/cu126"
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
    foreach ($v in @("3.12", "3.11", "3.10")) {
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

    Step "Installing PyTorch with CUDA (about 3 GB, takes a while)"
    & $vpy -m pip install torch torchvision --index-url $CudaIndex
    if ($LASTEXITCODE -ne 0) { throw "PyTorch installation failed" }

    Step "Installing Ultralytics, OpenVINO and the project"
    & $vpy -m pip install -e ".[train,openvino]"
    if ($LASTEXITCODE -ne 0) { throw "Installation failed" }

    Step "Checking the GPU"
    & $vpy -c "import torch; ok=torch.cuda.is_available(); print('CUDA:', ok, torch.version.cuda); print('GPU:', torch.cuda.get_device_name(0) if ok else '-'); raise SystemExit(0 if ok else 1)"
    if ($LASTEXITCODE -ne 0) { throw "No CUDA GPU visible - is the NVIDIA driver up to date?" }

    Step "Waiting for training data ($Data\READY)"
    while (-not (Test-Path "$Data\READY")) {
        $n = (Get-ChildItem -Recurse -Filter *.png "$Data" -ErrorAction SilentlyContinue | Measure-Object).Count
        Write-Host ("{0:HH:mm:ss}  {1} crops so far - waiting..." -f (Get-Date), $n)
        Start-Sleep -Seconds 30
    }

    Step "Training ($Epochs epochs)"
    & $vpy tools\train_classifier.py --data $Data --epochs $Epochs --device 0
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
