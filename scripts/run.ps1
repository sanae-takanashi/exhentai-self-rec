param(
    [string]$HostName = "",
    [string]$Port = "",
    [string]$DataDir = "",
    [string]$Proxy = "",
    [string]$VisualEncoder = "",
    [string]$DinoDevice = "",
    [string]$VenvPath = ".venv",
    [string]$HathTokenFile = $env:EXH_REC_HATH_TOKEN_FILE,
    [string]$HathSshConfig = $env:EXH_REC_HATH_SSH_CONFIG,
    [string]$HathSshHost = $env:EXH_REC_HATH_SSH_HOST,
    [switch]$NoHath
)

$ErrorActionPreference = "Stop"
$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$PythonExe = Join-Path $RepoRoot (Join-Path $VenvPath "Scripts\python.exe")

if (-not (Test-Path $PythonExe)) {
    throw "Missing $VenvPath. Run .\scripts\setup-venv.ps1 first, or .\scripts\setup-rocm-venv.ps1 for Windows ROCm."
}

if (-not $NoHath -and ($HathTokenFile -or $HathSshConfig)) {
    if (-not $HathTokenFile -or -not $HathSshConfig) {
        throw "Set both EXH_REC_HATH_TOKEN_FILE and EXH_REC_HATH_SSH_CONFIG to enable the H@H launcher."
    }
    $HathArgs = @{
        HostName = $HostName
        Port = $(if ($Port) { $Port } else { "18787" })
        DataDir = $DataDir
        Proxy = $Proxy
        VisualEncoder = $VisualEncoder
        DinoDevice = $DinoDevice
        VenvPath = $VenvPath
        TokenFile = $HathTokenFile
        SshConfig = $HathSshConfig
    }
    if ($HathSshHost) {
        $HathArgs.SshHost = $HathSshHost
    }
    & (Join-Path $PSScriptRoot "run-with-hath.ps1") @HathArgs
    exit $LASTEXITCODE
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
if ($VisualEncoder) {
    $env:EXH_REC_VISUAL_ENCODER = $VisualEncoder
}
if ($DinoDevice) {
    $env:EXH_REC_DINOV2_DEVICE = $DinoDevice
}

Set-Location $RepoRoot
& $PythonExe -m exh_rec.app
