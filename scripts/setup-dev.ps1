param([string]$BootstrapPython = "python")
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path -Parent $PSScriptRoot)
$env:PYTHONUTF8 = '1'
$env:UV_PYTHON_INSTALL_DIR = Join-Path $PWD '.tools/python'
$env:UV_CACHE_DIR = Join-Path $PWD '.tools/uv-cache'
$env:PLAYWRIGHT_BROWSERS_PATH = Join-Path $PWD '.tools/ms-playwright'
function Assert-Success {
    if ($LASTEXITCODE -ne 0) { throw "Command failed with exit code $LASTEXITCODE" }
}
$uv = Join-Path $PWD '.tools/uv-package/bin/uv.exe'
if (-not (Test-Path $uv)) {
    & $BootstrapPython -m pip install --target .tools/uv-package uv==0.13.1
    Assert-Success
}
& $uv python install 3.13.16 --no-bin
Assert-Success
if (-not (Test-Path .venv/Scripts/python.exe)) {
    & $uv venv --python 3.13.16 .venv
    Assert-Success
}
& ./.venv/Scripts/python.exe -c "import sys; assert sys.version_info[:3] == (3,13,16), 'Expected Python 3.13.16'"
Assert-Success
& $uv pip sync --python .venv/Scripts/python.exe requirements-dev.txt
Assert-Success
& ./.venv/Scripts/python.exe -m playwright install chromium
Assert-Success
& ./.venv/Scripts/python.exe scripts/verify.py
Assert-Success
