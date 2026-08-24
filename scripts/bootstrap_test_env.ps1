[CmdletBinding()]
param(
    [string]$PythonExe = 'python.exe',
    [string]$EnvironmentPath = '.venv-vv',
    [string]$LockPath = 'toolchain.lock.json'
)

$ErrorActionPreference = 'Stop'

if (-not (Get-Command $PythonExe -ErrorAction SilentlyContinue)) {
    throw "PYTHON_EXECUTABLE_NOT_FOUND:$PythonExe"
}

& $PythonExe -B -m toolchain_lock --lock $LockPath
if ($LASTEXITCODE -ne 0) {
    throw "TOOLCHAIN_LOCK_BLOCKED:$LockPath"
}

$version = (& $PythonExe -c "import platform,sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}|{platform.architecture()[0]}')").Trim()
$lock = Get-Content -LiteralPath $LockPath -Raw -Encoding UTF8 | ConvertFrom-Json
$expected = "$($lock.python.version)|64bit"
if ($version -ne $expected) {
    throw "PYTHON_IDENTITY_MISMATCH:expected=$expected actual=$version"
}

if (Test-Path -LiteralPath $EnvironmentPath) {
    throw "ENVIRONMENT_PATH_ALREADY_EXISTS:$EnvironmentPath"
}

& $PythonExe -m venv $EnvironmentPath
if ($LASTEXITCODE -ne 0) {
    throw "VENV_CREATE_FAILED"
}

$venvPython = Join-Path $EnvironmentPath 'Scripts\python.exe'
$pipHash = $lock.packages.pip.hashes | Select-Object -First 1
if ($null -eq $pipHash) {
    throw "PIP_WHEEL_HASH_MISSING"
}

$pipLockPath = Join-Path $EnvironmentPath '.bootstrap-pip.lock'
try {
    Set-Content -LiteralPath $pipLockPath -Value "pip==$($lock.pip.version) --hash=sha256:$pipHash" -Encoding ASCII
    & $venvPython -m pip install --require-hashes --disable-pip-version-check --index-url https://pypi.org/simple -r $pipLockPath
    if ($LASTEXITCODE -ne 0) {
        throw "PIP_INSTALL_FAILED"
    }
}
finally {
    Remove-Item -LiteralPath $pipLockPath -Force -ErrorAction SilentlyContinue
}

& $venvPython -m pip install --require-hashes --disable-pip-version-check --index-url https://pypi.org/simple -r requirements-dev.lock
if ($LASTEXITCODE -ne 0) {
    throw "LOCKED_DEPENDENCY_INSTALL_FAILED"
}

$installedPip = ((& $venvPython -m pip --version).Trim() -split '\s+')[1]
if ($installedPip -ne $lock.pip.version) {
    throw "PIP_IDENTITY_MISMATCH:expected=$($lock.pip.version) actual=$installedPip"
}

& $venvPython -m pytest --version
if ($LASTEXITCODE -ne 0) {
    throw "PYTEST_BOOTSTRAP_FAILED"
}
