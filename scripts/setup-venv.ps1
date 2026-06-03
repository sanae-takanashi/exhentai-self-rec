param(
    [ValidateSet("none", "cpu", "cuda")]
    [string]$Visual = "none",
    [string]$TorchIndexUrl = "",
    [string]$VenvPath = ".venv"
)

$ErrorActionPreference = "Stop"
$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$VenvFullPath = Join-Path $RepoRoot $VenvPath
$PythonExe = Join-Path $VenvFullPath "Scripts\python.exe"

function Invoke-ProjectPython {
    param([string[]]$Arguments)
    & $PythonExe @Arguments
}

if (-not (Test-Path $PythonExe)) {
    if (Get-Command py -ErrorAction SilentlyContinue) {
        & py -3 -m venv $VenvFullPath
    } else {
        & python -m venv $VenvFullPath
    }
}

Invoke-ProjectPython @("-m", "pip", "install", "--upgrade", "pip")
Invoke-ProjectPython @("-m", "pip", "install", "-r", (Join-Path $RepoRoot "requirements.txt"))

if ($Visual -ne "none") {
    if ($Visual -eq "cpu") {
        Invoke-ProjectPython @("-m", "pip", "install", "torch", "torchvision", "--index-url", "https://download.pytorch.org/whl/cpu")
    } elseif ($TorchIndexUrl) {
        Invoke-ProjectPython @("-m", "pip", "install", "torch", "torchvision", "--index-url", $TorchIndexUrl)
    } else {
        Invoke-ProjectPython @("-m", "pip", "install", "torch", "torchvision")
    }
    Invoke-ProjectPython @("-m", "pip", "install", "-r", (Join-Path $RepoRoot "requirements-visual.txt"))
}

Write-Host "Virtual environment ready: $VenvFullPath"
Write-Host "Run with: .\scripts\run.ps1"
