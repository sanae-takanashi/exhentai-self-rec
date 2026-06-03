param(
    [string]$HostName = "",
    [string]$Port = "",
    [string]$DataDir = "",
    [string]$Proxy = "",
    [string]$DinoDevice = ""
)

$ErrorActionPreference = "Stop"
$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$PythonExe = Join-Path $RepoRoot ".venv\Scripts\python.exe"

if (-not (Test-Path $PythonExe)) {
    throw "Missing .venv. Run .\scripts\setup-venv.ps1 first."
}

if ($HostName) {
    $env:EXH_REC_HOST = $HostName
}
if ($Port) {
    $env:EXH_REC_PORT = $Port
}
if ($DataDir) {
    $env:EXH_REC_DATA_DIR = $DataDir
}
if ($Proxy) {
    $env:EXH_REC_PROXY = $Proxy
}
if ($DinoDevice) {
    $env:EXH_REC_DINOV2_DEVICE = $DinoDevice
}

Set-Location $RepoRoot
& $PythonExe -m exh_rec.app
