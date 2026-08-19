param(
    [string]$HostName = "",
    [string]$Port = "18787",
    [string]$DataDir = "",
    [string]$Proxy = "",
    [string]$VisualEncoder = "",
    [string]$DinoDevice = "",
    [string]$VenvPath = ".venv",
    [string]$TokenFile = $env:EXH_REC_HATH_TOKEN_FILE,
    [string]$SshConfig = $env:EXH_REC_HATH_SSH_CONFIG,
    [string]$SshHost = $env:EXH_REC_HATH_SSH_HOST,
    [string]$SshExecutable = "",
    [int]$RemotePort = 18788,
    [int]$ReconnectSeconds = 5
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$PythonExe = Join-Path $RepoRoot (Join-Path $VenvPath "Scripts\python.exe")

if (-not $SshHost) {
    $SshHost = "exh-rec-hath"
}
if (-not $SshExecutable) {
    $SshExecutable = Join-Path $env:WINDIR "System32\OpenSSH\ssh.exe"
}
if (-not (Test-Path $PythonExe -PathType Leaf)) {
    throw "Missing $VenvPath. Run .\scripts\setup-venv.ps1 first, or .\scripts\setup-rocm-venv.ps1 for Windows ROCm."
}
if (-not (Test-Path $TokenFile -PathType Leaf)) {
    throw "Missing H@H token file: $TokenFile"
}
if (-not (Test-Path $SshConfig -PathType Leaf)) {
    throw "Missing H@H SSH config: $SshConfig"
}
if (-not (Test-Path $SshExecutable -PathType Leaf)) {
    throw "Missing OpenSSH client: $SshExecutable"
}

if ($HostName) {
    $env:EXH_REC_HOST = $HostName
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
$LauncherArgs = @(
    ('"{0}"' -f (Join-Path $PSScriptRoot "run_with_hath.py")),
    "--token-file", ('"{0}"' -f $TokenFile),
    "--ssh-config", ('"{0}"' -f $SshConfig),
    "--ssh-host", $SshHost,
    "--ssh-executable", ('"{0}"' -f $SshExecutable),
    "--local-port", ([int]$Port),
    "--remote-port", $RemotePort,
    "--reconnect-seconds", $ReconnectSeconds
)
$Launcher = Start-Process -FilePath $PythonExe -ArgumentList $LauncherArgs -NoNewWindow -PassThru
try {
    $Launcher.WaitForExit()
} finally {
    if (-not $Launcher.HasExited) {
        Stop-Process -Id $Launcher.Id
    }
}
exit ([int]$Launcher.ExitCode)
