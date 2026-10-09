[CmdletBinding()]
param(
    # Pinned official python.org installer. Python 3.14 is the last series
    # with this classic installer besides 3.15; 3.16+ only ships the Python
    # install manager.
    [string]$InstallerUrl = "https://www.python.org/ftp/python/3.14.8/python-3.14.8-amd64.exe",
    [string]$TargetDirectory = (Join-Path $env:LOCALAPPDATA "Programs\Python\Python314"),
    [string]$InstallerLog = "",
    # Download and check the signature only; nothing is installed.
    [switch]$VerifyOnly
)

# Installs Python for the current user after consent in setup.bat: no
# administrator rights, no PATH change. The py launcher is left out because
# its per-user variant prepends its folder to the user PATH and would shadow
# the py command of the Python install manager; setup.bat finds the
# interpreter in TargetDirectory instead.
#
# Exit codes (setup.bat logs them and reports 10):
#   70 download failed, 71 signature invalid or wrong publisher,
#   72 installer failed, 73 python.exe missing after installation.

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

$downloadDirectory = Join-Path `
    ([IO.Path]::GetTempPath()) `
    ("pdf-umbenenner-python-" + [Guid]::NewGuid().ToString("N"))
$installerName = [IO.Path]::GetFileName(([Uri]$InstallerUrl).LocalPath)
if (-not $installerName) {
    $installerName = "python-installer.exe"
}
$temporaryInstaller = Join-Path $downloadDirectory $installerName

try {
    try {
        New-Item -ItemType Directory -Path $downloadDirectory | Out-Null
        Write-Output ("PYTHON_DOWNLOAD: " + $InstallerUrl)
        [Net.ServicePointManager]::SecurityProtocol = `
            [Net.ServicePointManager]::SecurityProtocol -bor `
            [Net.SecurityProtocolType]::Tls12
        # Company proxies usually expect the Windows logon credentials.
        $proxy = [Net.WebRequest]::DefaultWebProxy
        if ($null -ne $proxy) {
            $proxy.Credentials = [Net.CredentialCache]::DefaultNetworkCredentials
        }
        # The progress bar slows downloads in Windows PowerShell 5.1 a lot.
        $ProgressPreference = "SilentlyContinue"
        Invoke-WebRequest -Uri $InstallerUrl -OutFile $temporaryInstaller -UseBasicParsing
    } catch {
        Stop-Installer 70 "PYTHON_DOWNLOAD_FAILED" $_.Exception.Message
    }

    $signature = Get-AuthenticodeSignature -LiteralPath $temporaryInstaller
    $subject = if ($null -ne $signature.SignerCertificate) {
        $signature.SignerCertificate.Subject
    } else {
        ""
    }
    if (
        [string]$signature.Status -ne "Valid" -or
        $subject -notmatch "(?:^|,\s*)CN=`"?Python Software Foundation`"?(?:,|$)"
    ) {
        Stop-Installer 71 "PYTHON_SIGNATURE_INVALID" `
            ("Status=" + $signature.Status + "; Signer=" + $subject)
    }
    Write-Output ("PYTHON_SIGNATURE_OK: " + $subject + "; Thumbprint=" + $signature.SignerCertificate.Thumbprint)

    if ($VerifyOnly) {
        Write-Output "PYTHON_VERIFY_ONLY: Es wurde nichts installiert."
        exit 0
    }

    $arguments = @(
        "/quiet",
        "InstallAllUsers=0",
        "PrependPath=0",
        "Include_launcher=0",
        "AssociateFiles=0",
        "Shortcuts=0",
        "Include_tcltk=1",
        "Include_pip=1",
        "Include_doc=0",
        "Include_test=0",
        ('TargetDir="' + $TargetDirectory + '"')
    )
    if ($InstallerLog) {
        $arguments += @("/log", ('"' + $InstallerLog + '"'))
    }
    Write-Output "PYTHON_INSTALL: Python wird fuer den aktuellen Benutzer installiert."
    try {
        $process = Start-Process `
            -FilePath $temporaryInstaller `
            -ArgumentList $arguments `
            -Wait `
            -PassThru
    } catch {
        Stop-Installer 72 "PYTHON_INSTALL_FAILED" $_.Exception.Message
    }
    if ($process.ExitCode -notin 0, 3010) {
        Stop-Installer 72 "PYTHON_INSTALL_FAILED" `
            ("Installer-Rueckgabecode: " + $process.ExitCode)
    }

    $python = Join-Path $TargetDirectory "python.exe"
    if (-not (Test-Path -LiteralPath $python)) {
        Stop-Installer 73 "PYTHON_MISSING_AFTER_INSTALL" $python
    }
    Write-Output ("PYTHON_INSTALL_OK: " + $python)
} finally {
    if (Test-Path -LiteralPath $downloadDirectory) {
        Remove-Item -LiteralPath $downloadDirectory -Recurse -Force -ErrorAction SilentlyContinue
    }
}

exit 0
