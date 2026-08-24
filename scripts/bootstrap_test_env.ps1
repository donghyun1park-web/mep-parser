[CmdletBinding()]
param(
    [string]$PythonExe = 'python.exe',
    [string]$EnvironmentPath = '.venv-vv',
    [string]$LockPath = 'toolchain.lock.json',
    [string]$RequirementsLock = 'requirements-dev.lock'
)

$ErrorActionPreference = 'Stop'

$pythonCommand = Get-Command $PythonExe -ErrorAction SilentlyContinue
if ($null -eq $pythonCommand) {
    throw "PYTHON_EXECUTABLE_NOT_FOUND:$PythonExe"
}
$pythonPath = [IO.Path]::GetFullPath($pythonCommand.Source)
if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf) -or [IO.Path]::GetExtension($pythonPath) -ine '.exe') {
    throw "PYTHON_EXECUTABLE_NOT_EXE:$PythonExe"
}

$lock = Get-Content -LiteralPath $LockPath -Raw -Encoding UTF8 | ConvertFrom-Json
$expectedExecutableHash = [string]$lock.python.executable_sha256
$expectedSignerThumbprint = [string]$lock.python.signer_thumbprint
if ($expectedExecutableHash -notmatch '^[0-9a-fA-F]{64}$' -or $expectedSignerThumbprint -notmatch '^[0-9a-fA-F]{40}$') {
    throw "TOOLCHAIN_LOCK_AUTHENTICATION_FIELDS_MISSING:$LockPath"
}
$hashStream = [IO.File]::OpenRead($pythonPath)
try {
    $hashAlgorithm = [Security.Cryptography.SHA256]::Create()
    try {
        $actualExecutableHash = ([BitConverter]::ToString($hashAlgorithm.ComputeHash($hashStream))).Replace('-', '')
    }
    finally {
        $hashAlgorithm.Dispose()
    }
}
finally {
    $hashStream.Dispose()
}
if ($actualExecutableHash -ine $expectedExecutableHash) {
    throw "PYTHON_EXECUTABLE_HASH_MISMATCH:expected=$expectedExecutableHash actual=$actualExecutableHash"
}
$signature = $null
try {
    $signature = Get-AuthenticodeSignature -LiteralPath $pythonPath -ErrorAction Stop
}
catch {
    if (-not ("ToolchainWinTrust" -as [type])) {
        Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class ToolchainWinTrust {
    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    private struct WinTrustFileInfo {
        public uint cbStruct;
        public IntPtr pcwszFilePath;
        public IntPtr hFile;
        public IntPtr pgKnownSubject;
    }
    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    private struct WinTrustData {
        public uint cbStruct;
        public IntPtr pPolicyCallbackData;
        public IntPtr pSIPClientData;
        public uint dwUIChoice;
        public uint fdwRevocationChecks;
        public uint dwUnionChoice;
        public IntPtr pFile;
        public uint dwStateAction;
        public IntPtr hWVTStateData;
        public IntPtr pwszURLReference;
        public uint dwProvFlags;
        public uint dwUIContext;
        public IntPtr pSignatureSettings;
    }
    [DllImport("wintrust.dll", ExactSpelling = true, CharSet = CharSet.Unicode)]
    private static extern uint WinVerifyTrust(IntPtr hwnd, [MarshalAs(UnmanagedType.LPStruct)] Guid actionID, IntPtr data);
    public static bool HasValidSignature(string filePath) {
        IntPtr filePathPointer = IntPtr.Zero;
        IntPtr fileInfoPointer = IntPtr.Zero;
        IntPtr dataPointer = IntPtr.Zero;
        try {
            filePathPointer = Marshal.StringToCoTaskMemUni(filePath);
            var fileInfo = new WinTrustFileInfo {
                cbStruct = (uint)Marshal.SizeOf(typeof(WinTrustFileInfo)),
                pcwszFilePath = filePathPointer
            };
            fileInfoPointer = Marshal.AllocCoTaskMem(Marshal.SizeOf(typeof(WinTrustFileInfo)));
            Marshal.StructureToPtr(fileInfo, fileInfoPointer, false);
            var data = new WinTrustData {
                cbStruct = (uint)Marshal.SizeOf(typeof(WinTrustData)),
                dwUIChoice = 2,
                fdwRevocationChecks = 0,
                dwUnionChoice = 1,
                pFile = fileInfoPointer,
                dwStateAction = 0,
                dwProvFlags = 0x00000040,
                dwUIContext = 0
            };
            dataPointer = Marshal.AllocCoTaskMem(Marshal.SizeOf(typeof(WinTrustData)));
            Marshal.StructureToPtr(data, dataPointer, false);
            return WinVerifyTrust(IntPtr.Zero, new Guid("00AAC56B-CD44-11D0-8CC2-00C04FC295EE"), dataPointer) == 0;
        }
        finally {
            if (dataPointer != IntPtr.Zero) Marshal.FreeCoTaskMem(dataPointer);
            if (fileInfoPointer != IntPtr.Zero) Marshal.FreeCoTaskMem(fileInfoPointer);
            if (filePathPointer != IntPtr.Zero) Marshal.FreeCoTaskMem(filePathPointer);
        }
    }
}
'@
    }
    if (-not [ToolchainWinTrust]::HasValidSignature($pythonPath)) {
        throw "PYTHON_EXECUTABLE_SIGNATURE_INVALID:WinVerifyTrust"
    }
    $signerThumbprint = [Security.Cryptography.X509Certificates.X509Certificate]::CreateFromSignedFile($pythonPath).GetCertHashString()
}
if ($null -ne $signature -and $signature.Status -ne 'Valid') {
    throw "PYTHON_EXECUTABLE_SIGNATURE_INVALID:$($signature.Status)"
}
if ($null -ne $signature) {
    $signerThumbprint = $signature.SignerCertificate.Thumbprint
}
if ([string]::IsNullOrWhiteSpace($signerThumbprint) -or $signerThumbprint -ine $expectedSignerThumbprint) {
    throw "PYTHON_EXECUTABLE_SIGNER_MISMATCH"
}

& $pythonPath -B -m toolchain_lock --lock $LockPath --requirements-lock $RequirementsLock
if ($LASTEXITCODE -ne 0) {
    throw "TOOLCHAIN_LOCK_BLOCKED:$LockPath"
}

$version = (& $pythonPath -c "import platform,sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}|{platform.architecture()[0]}')").Trim()
$expected = "$($lock.python.version)|64bit"
if ($version -ne $expected) {
    throw "PYTHON_IDENTITY_MISMATCH:expected=$expected actual=$version"
}

if (Test-Path -LiteralPath $EnvironmentPath) {
    throw "ENVIRONMENT_PATH_ALREADY_EXISTS:$EnvironmentPath"
}

try {
    & $pythonPath -m venv --without-pip $EnvironmentPath
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
        $venvSitePackages = Join-Path $EnvironmentPath 'Lib\site-packages'
        & $pythonPath -m pip install --target $venvSitePackages --require-hashes --disable-pip-version-check --index-url https://pypi.org/simple -r $pipLockPath
        if ($LASTEXITCODE -ne 0) {
            throw "PIP_INSTALL_FAILED"
        }
    }
    finally {
        if (Test-Path -LiteralPath $pipLockPath) {
            Remove-Item -LiteralPath $pipLockPath -Force -ErrorAction Stop
        }
    }

    $pipMetadata = @(Get-ChildItem -LiteralPath $venvSitePackages -Directory -Filter 'pip-*.dist-info')
    if ($pipMetadata.Count -ne 1 -or $pipMetadata[0].Name -ne "pip-$($lock.pip.version).dist-info") {
        throw "PIP_METADATA_IDENTITY_MISMATCH"
    }

    & $venvPython -m pip install --require-hashes --disable-pip-version-check --index-url https://pypi.org/simple -r $RequirementsLock
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
}
catch {
    $failure = $_
    if (Test-Path -LiteralPath $EnvironmentPath) {
        $cleanupFailure = $null
        for ($attempt = 1; $attempt -le 10 -and (Test-Path -LiteralPath $EnvironmentPath); $attempt++) {
            try {
                Remove-Item -LiteralPath $EnvironmentPath -Recurse -Force -ErrorAction Stop
                $cleanupFailure = $null
            }
            catch {
                $cleanupFailure = $_
                Start-Sleep -Seconds 1
            }
        }
        if (Test-Path -LiteralPath $EnvironmentPath) {
            throw "ENVIRONMENT_CLEANUP_FAILED:original=$($failure.Exception.Message) cleanup=$($cleanupFailure.Exception.Message)"
        }
    }
    throw $failure
}
