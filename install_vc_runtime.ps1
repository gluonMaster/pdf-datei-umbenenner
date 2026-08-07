[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Stop-Installer {
    param(
        [int]$Code,
        [string]$Category,
        [string]$Message
    )

    [Console]::Error.WriteLine($Category + ": " + $Message)
    exit $Code
}

try {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    $isAdministrator = $principal.IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator
    )
} catch {
    Stop-Installer 61 "VC_RUNTIME_PRIVILEGE_CHECK_FAILED" $_.Exception.Message
}

if (-not $isAdministrator) {
    Stop-Installer 61 "VC_RUNTIME_ADMIN_REQUIRED" `
        "Die automatische Laufzeitinstallation erfordert eine als Administrator gestartete Konsole."
}

if ([Environment]::Is64BitOperatingSystem) {
    $downloadUri = "https://aka.ms/vc14/vc_redist.x64.exe"
} else {
    $downloadUri = "https://aka.ms/vc14/vc_redist.x86.exe"
}

$temporaryInstaller = Join-Path `
    ([IO.Path]::GetTempPath()) `
    ("rename-engine-vc-redist-" + [Guid]::NewGuid().ToString("N") + ".exe")

try {
    Write-Output "VC_RUNTIME_DOWNLOAD: Microsoft Visual C++ Redistributable wird geladen."
    [Net.ServicePointManager]::SecurityProtocol = `
        [Net.ServicePointManager]::SecurityProtocol -bor `
        [Net.SecurityProtocolType]::Tls12
    Invoke-WebRequest -Uri $downloadUri -OutFile $temporaryInstaller -UseBasicParsing
} catch {
    if (Test-Path -LiteralPath $temporaryInstaller) {
        Remove-Item -LiteralPath $temporaryInstaller -Force -ErrorAction SilentlyContinue
    }
    Stop-Installer 62 "VC_RUNTIME_DOWNLOAD_FAILED" $_.Exception.Message
}

try {
    $signature = Get-AuthenticodeSignature -LiteralPath $temporaryInstaller
    $subject = if ($null -ne $signature.SignerCertificate) {
        $signature.SignerCertificate.Subject
    } else {
        ""
    }
    if (
        [string]$signature.Status -ne "Valid" -or
        $subject -notmatch "CN=Microsoft Corporation(?:,|$)"
    ) {
        Stop-Installer 63 "VC_RUNTIME_SIGNATURE_INVALID" `
            ("Status=" + $signature.Status + "; Signer=" + $subject)
    }

    Write-Output "VC_RUNTIME_SIGNATURE_OK: Die Microsoft-Signatur ist gueltig."
    $process = Start-Process `
        -FilePath $temporaryInstaller `
        -ArgumentList "/install", "/quiet", "/norestart" `
        -Wait `
        -PassThru `
        -WindowStyle Hidden
    if ($process.ExitCode -notin 0, 1638, 3010) {
        Stop-Installer 64 "VC_RUNTIME_INSTALL_FAILED" `
            ("Installer-Rueckgabecode: " + $process.ExitCode)
    }
    if ($process.ExitCode -eq 3010) {
        Write-Output "VC_RUNTIME_RESTART_RECOMMENDED: Windows meldet einen ausstehenden Neustart."
    }
    Write-Output "VC_RUNTIME_INSTALL_OK: Microsoft Visual C++ Redistributable wurde verarbeitet."
} catch {
    Stop-Installer 64 "VC_RUNTIME_INSTALL_FAILED" $_.Exception.Message
} finally {
    if (Test-Path -LiteralPath $temporaryInstaller) {
        Remove-Item -LiteralPath $temporaryInstaller -Force -ErrorAction SilentlyContinue
    }
}

exit 0
