param(
    [string]$VenvPath = ".venv-rocm",
    [string]$DinoDevice = "rocm",
    [switch]$SkipAppCheck
)

$ErrorActionPreference = "Stop"
$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$PythonExe = Join-Path $RepoRoot (Join-Path $VenvPath "Scripts\python.exe")

if (-not (Test-Path $PythonExe)) {
    throw "Missing $VenvPath. Run .\scripts\setup-rocm-venv.ps1 first."
}

$env:EXH_REC_DINOV2_DEVICE = $DinoDevice
$env:PYTHONPATH = [string]$RepoRoot

$CheckScript = @'
import json
import os
import platform
import sys


def print_row(name, value):
    print(f"{name}: {value}")


print_row("python", sys.version.replace("\n", " "))
print_row("executable", sys.executable)
print_row("platform", platform.platform())

try:
    import torch
except Exception as exc:
    print_row("torch_import", f"FAILED: {type(exc).__name__}: {exc}")
    raise SystemExit(2)

print_row("torch", torch.__version__)
print_row("torch.version.cuda", getattr(torch.version, "cuda", None))
print_row("torch.version.hip", getattr(torch.version, "hip", None))

hip_version = getattr(torch.version, "hip", None)
if not hip_version:
    print("RESULT: ROCm is NOT enabled in this PyTorch install because torch.version.hip is empty.")
    raise SystemExit(3)

cuda_available = bool(torch.cuda.is_available())
device_count = int(torch.cuda.device_count()) if hasattr(torch.cuda, "device_count") else 0
print_row("torch.cuda.is_available", cuda_available)
print_row("torch.cuda.device_count", device_count)

if not cuda_available or device_count < 1:
    print("RESULT: ROCm PyTorch is installed, but no AMD GPU is visible to torch.cuda.")
    raise SystemExit(4)

for index in range(device_count):
    try:
        name = torch.cuda.get_device_name(index)
    except Exception as exc:
        name = f"FAILED: {type(exc).__name__}: {exc}"
    print_row(f"torch.cuda.device[{index}]", name)

try:
    device = torch.device("cuda:0")
    a = torch.randn((256, 256), device=device)
    b = torch.randn((256, 256), device=device)
    c = a @ b
    torch.cuda.synchronize()
    print_row("tensor_test_device", str(c.device))
    print_row("tensor_test_mean", float(c.mean().detach().cpu()))
except Exception as exc:
    print_row("tensor_test", f"FAILED: {type(exc).__name__}: {exc}")
    raise SystemExit(5)

if os.environ.get("EXH_REC_SKIP_APP_CHECK") != "1":
    try:
        from exh_rec.visual import dinov2_dependency_status

        status = dinov2_dependency_status(os.environ.get("EXH_REC_DINOV2_DEVICE", "rocm"))
        print_row("app_dinov2_status", json.dumps(status, ensure_ascii=False, sort_keys=True))
        if not status.get("available"):
            print("RESULT: ROCm works in PyTorch, but the app's DINOv2 dependency check is not available.")
            raise SystemExit(6)
    except SystemExit:
        raise
    except Exception as exc:
        print_row("app_dinov2_status", f"FAILED: {type(exc).__name__}: {exc}")
        raise SystemExit(6)

print("RESULT: ROCm is enabled and usable by PyTorch. The app can use DINOv2 on this GPU path.")
'@

$TempScript = Join-Path ([System.IO.Path]::GetTempPath()) ("exh-rec-check-rocm-" + [System.Guid]::NewGuid().ToString("N") + ".py")
Set-Content -Path $TempScript -Value $CheckScript -Encoding UTF8

try {
    if ($SkipAppCheck) {
        $env:EXH_REC_SKIP_APP_CHECK = "1"
    } else {
        Remove-Item Env:\EXH_REC_SKIP_APP_CHECK -ErrorAction SilentlyContinue
    }
    & $PythonExe $TempScript
    if ($LASTEXITCODE -ne 0) {
        throw "ROCm check failed with exit code $LASTEXITCODE."
    }
} finally {
    Remove-Item $TempScript -ErrorAction SilentlyContinue
    Remove-Item Env:\EXH_REC_SKIP_APP_CHECK -ErrorAction SilentlyContinue
}
