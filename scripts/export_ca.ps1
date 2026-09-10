#Requires -Version 5.1
<#
Export public CA certificates from a corporate PFX/P12 into a PEM trust bundle.
No downloads, certificate store changes, or private key exports are performed.
Passwords are prompted locally; never pass a plaintext password on the command line.
#>
[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [string]$PfxPath,
    [Parameter(Position = 1)]
    [string]$OutputPath,
    [Security.SecureString]$Password,
    [switch]$Force
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$certificates = New-Object Security.Cryptography.X509Certificates.X509Certificate2Collection
$ownsPassword = $false
$passwordPointer = [IntPtr]::Zero
$plainPassword = $null
$temporaryPath = $null
$exitCode = 0

try {
    if ([string]::IsNullOrWhiteSpace($OutputPath)) {
        $OutputPath = Join-Path (Split-Path $PSScriptRoot -Parent) 'certs\corporate-ca.pem'
    }
    if ([string]::IsNullOrWhiteSpace($PfxPath)) {
        $PfxPath = (Read-Host 'Corporate CA PFX/P12 file path').Trim().Trim('"')
    }
    $inputFile = Get-Item -LiteralPath $PfxPath
    if ($inputFile.PSIsContainer -or $inputFile.Extension -notin @('.pfx', '.p12')) {
        throw 'Input must be a .pfx or .p12 file.'
    }
    $outputFile = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($OutputPath)
    if ($inputFile.FullName -eq $outputFile) {
        throw 'Input and output paths must be different. The original PFX is never overwritten.'
    }
    if ([IO.Path]::GetExtension($outputFile) -ne '.pem') {
        throw 'Output must have a .pem extension.'
    }
    if ((Test-Path -LiteralPath $outputFile) -and -not $Force) {
        throw 'Output already exists. Choose another output path or use -Force to replace the PEM.'
    }
    if (-not [Enum]::IsDefined(
        [Security.Cryptography.X509Certificates.X509KeyStorageFlags], 'EphemeralKeySet'
    )) {
        throw 'This tool requires .NET Framework 4.7.2+ (or PowerShell 7) for in-memory PFX import.'
    }
    if (-not $PSBoundParameters.ContainsKey('Password')) {
        $Password = Read-Host 'PFX password (hidden; press Enter if none)' -AsSecureString
        $ownsPassword = $true
    }
    if ($null -eq $Password) {
        throw 'Password must be a SecureString. Omit -Password to use the hidden local prompt.'
    }

    try {
        # The Framework collection API accepts a string password. Clear the temporary
        # unmanaged buffer immediately after import; never log or save the password.
        $passwordPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($Password)
        $plainPassword = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($passwordPointer)
        $certificates.Import(
            $inputFile.FullName, $plainPassword,
            [Security.Cryptography.X509Certificates.X509KeyStorageFlags]::EphemeralKeySet
        )
    }
    catch {
        throw ('Cannot open PFX. Check its password, file integrity and Windows encryption support. ' +
            $_.Exception.GetBaseException().Message)
    }
    finally {
        $plainPassword = $null
        if ($passwordPointer -ne [IntPtr]::Zero) {
            [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($passwordPointer)
            $passwordPointer = [IntPtr]::Zero
        }
    }

    $pem = New-Object Text.StringBuilder
    $seen = @{}
    $exported = @()
    foreach ($certificate in $certificates) {
        $isCA = $false
        foreach ($extension in $certificate.Extensions) {
            if ($extension.Oid.Value -eq '2.5.29.19') {
                $constraints = New-Object Security.Cryptography.X509Certificates.X509BasicConstraintsExtension
                $constraints.CopyFrom($extension)
                $isCA = $constraints.CertificateAuthority
                break
            }
        }
        if (-not $isCA -or $seen.ContainsKey($certificate.Thumbprint)) {
            continue
        }
        $seen[$certificate.Thumbprint] = $true
        # RawData is the public X.509 certificate only, even if the PFX contains keys.
        $encoded = [Convert]::ToBase64String($certificate.RawData)
        [void]$pem.AppendLine('-----BEGIN CERTIFICATE-----')
        foreach ($line in [regex]::Matches($encoded, '.{1,64}')) {
            [void]$pem.AppendLine($line.Value)
        }
        [void]$pem.AppendLine('-----END CERTIFICATE-----')
        $exported += $certificate
    }
    if ($exported.Count -eq 0) {
        throw ('No CA certificates (Basic Constraints CA=true) found in the PFX. ' +
            'Request a root/intermediate CA certificate bundle from your certificate administrator.')
    }

    $outputDirectory = [IO.Path]::GetDirectoryName($outputFile)
    [void][IO.Directory]::CreateDirectory($outputDirectory)
    $temporaryPath = Join-Path $outputDirectory ([IO.Path]::GetRandomFileName() + '.tmp')
    [IO.File]::WriteAllText($temporaryPath, $pem.ToString(), [Text.Encoding]::ASCII)
    # Finish parsing before replacing an existing bundle. A bad password or bad PFX
    # leaves the previous bundle untouched; readers see a complete file.
    if ($Force -and [IO.File]::Exists($outputFile)) {
        [IO.File]::Replace($temporaryPath, $outputFile, [NullString]::Value)
    }
    else {
        [IO.File]::Move($temporaryPath, $outputFile)
    }
    $temporaryPath = $null

    Write-Host ('Exported {0} public CA certificate(s): {1}' -f $exported.Count, $outputFile)
    foreach ($certificate in $exported) {
        Write-Host ('  {0} | SHA1 thumbprint: {1} | expires: {2:yyyy-MM-dd}' -f
            $certificate.Subject, $certificate.Thumbprint, $certificate.NotAfter)
        if ($certificate.NotAfter -lt (Get-Date) -or $certificate.NotBefore -gt (Get-Date)) {
            Write-Warning 'This CA certificate is outside its validity period. Obtain a current CA bundle.'
        }
    }
    Write-Host 'Set these values in .env, then restart the app/worker:'
    Write-Host 'TLS_VERIFY=true'
    Write-Host ('CA_BUNDLE="{0}"' -f $outputFile.Replace('\', '/'))
    Write-Host 'The original PFX, .env and Windows certificate stores were not changed.'
}
catch {
    [Console]::Error.WriteLine('[error] ' + $_.Exception.Message)
    $exitCode = 1
}
finally {
    foreach ($certificate in $certificates) {
        $certificate.Dispose()
    }
    if ($ownsPassword -and $null -ne $Password) {
        $Password.Dispose()
    }
    if ($temporaryPath -and [IO.File]::Exists($temporaryPath)) {
        [IO.File]::Delete($temporaryPath)
    }
}
exit $exitCode
