param(
    [string]$VenvPath = ".venv-rocm",
    [string]$Python = "",
    [string]$Proxy = "",
    [switch]$SkipProjectDeps
)

$ErrorActionPreference = "Stop"
$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$VenvFullPath = Join-Path $RepoRoot $VenvPath
$PythonExe = Join-Path $VenvFullPath "Scripts\python.exe"

$RocmVersion = "7.2.1"
$RocmBase = "https://repo.radeon.com/rocm/windows/rocm-rel-$RocmVersion"
$RocmPackages = @(
    "$RocmBase/rocm_sdk_core-$RocmVersion-py3-none-win_amd64.whl",
    "$RocmBase/rocm_sdk_devel-$RocmVersion-py3-none-win_amd64.whl",
    "$RocmBase/rocm_sdk_libraries_custom-$RocmVersion-py3-none-win_amd64.whl",
    "$RocmBase/rocm-$RocmVersion.tar.gz"
)
$TorchPackages = @(
    "$RocmBase/torch-2.9.1%2Brocm$RocmVersion-cp312-cp312-win_amd64.whl",
    "$RocmBase/torchaudio-2.9.1%2Brocm$RocmVersion-cp312-cp312-win_amd64.whl",
    "$RocmBase/torchvision-0.24.1%2Brocm$RocmVersion-cp312-cp312-win_amd64.whl"
)

function Invoke-ProjectPython {
    param([string[]]$Arguments)
    & $PythonExe @Arguments
}

function Invoke-PipInstall {
    param([string[]]$Arguments)
    $PipArgs = @("-m", "pip", "install")
    if ($Proxy) {
        $PipArgs += @("--proxy", $Proxy)
    }
    Invoke-ProjectPython ($PipArgs + $Arguments)
}

function Get-PythonVersion {
    param([string]$Exe)
    $VersionText = & $Exe -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
    if ($LASTEXITCODE -ne 0) {
        throw "Could not run Python at $Exe"
    }
    return $VersionText.Trim()
}

function Get-Python312RequiredMessage {
    return @"
Python 3.12 is required for AMD ROCm Windows PyTorch wheels, but the script could not find it.

Install it, then rerun this script:
  winget install -e --id Python.Python.3.12

After install, open a new PowerShell window and check:
  py -3.12 -V

If Python 3.12 is installed outside the py launcher, pass it explicitly:
  .\scripts\setup-rocm-venv.ps1 -Python "C:\Path\To\python.exe"
"@
}

if (-not (Test-Path $PythonExe)) {
    if ($Python) {
        if (-not (Test-Path $Python)) {
            throw "The -Python path does not exist: $Python"
        }
        $SelectedPythonVersion = Get-PythonVersion $Python
        if ($SelectedPythonVersion -ne "3.12") {
            throw "ROCm Windows PyTorch wheels require Python 3.12, but -Python is Python $SelectedPythonVersion."
        }
        & $Python -m venv $VenvFullPath
    } elseif (Get-Command py -ErrorAction SilentlyContinue) {
        & py -3.12 -c "import sys; print(sys.executable)" *> $null
        if ($LASTEXITCODE -ne 0) {
            throw (Get-Python312RequiredMessage)
        }
        & py -3.12 -m venv $VenvFullPath
    } else {
        throw (Get-Python312RequiredMessage)
    }
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $PythonExe)) {
        throw "Failed to create virtual environment at $VenvFullPath."
    }
}

$VenvPythonVersion = Get-PythonVersion $PythonExe
if ($VenvPythonVersion -ne "3.12") {
    throw "ROCm Windows PyTorch wheels require Python 3.12, but $PythonExe is Python $VenvPythonVersion. Choose a new -VenvPath or recreate this venv with Python 3.12."
}

Invoke-ProjectPython @("-m", "pip", "install", "--upgrade", "pip")

if (-not $SkipProjectDeps) {
    Invoke-PipInstall @("-r", (Join-Path $RepoRoot "requirements.txt"))
}

Invoke-PipInstall (@("--no-cache-dir") + $RocmPackages)
Invoke-PipInstall (@("--no-cache-dir") + $TorchPackages)
Invoke-PipInstall @("-r", (Join-Path $RepoRoot "requirements-visual.txt"))

Write-Host ""
Write-Host "Verifying PyTorch ROCm..."
Invoke-ProjectPython @("-c", "import torch; print('torch', torch.__version__); print('hip', getattr(torch.version, 'hip', None)); print('cuda_available', torch.cuda.is_available()); print('device_count', torch.cuda.device_count()); print('device', torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'none')")

Write-Host ""
Write-Host "ROCm virtual environment ready: $VenvFullPath"
Write-Host "Run with: .\scripts\run.ps1 -VenvPath $VenvPath -VisualEncoder dinov2 -DinoDevice auto"
Write-Host "You can also use -DinoDevice rocm; the app maps it to PyTorch's cuda device API for ROCm builds."
