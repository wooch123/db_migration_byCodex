# Test certificates only. All keys are generated in memory for the local test.
# No certificate stores or external servers are used.
using namespace System.Security.Cryptography
using namespace System.Security.Cryptography.X509Certificates
param([Parameter(Mandatory = $true)][string]$OutputDirectory)
$ErrorActionPreference = 'Stop'

function New-TestRequest([string]$Subject, [RSA]$Key, [bool]$IsCA) {
    $request = [CertificateRequest]::new($Subject, $Key, [HashAlgorithmName]::SHA256, [RSASignaturePadding]::Pkcs1)
    $request.CertificateExtensions.Add([X509BasicConstraintsExtension]::new($IsCA, $false, 0, $true))
    $usage = [X509KeyUsageFlags]::DigitalSignature -bor [X509KeyUsageFlags]::KeyEncipherment
    if ($IsCA) { $usage = [X509KeyUsageFlags]::KeyCertSign -bor [X509KeyUsageFlags]::CrlSign }
    $request.CertificateExtensions.Add([X509KeyUsageExtension]::new($usage, $true))
    $request.CertificateExtensions.Add([X509SubjectKeyIdentifierExtension]::new($request.PublicKey, $false))
    return $request
}

$rootKey = [RSACng]::new(2048)
$intermediateKey = [RSACng]::new(2048)
$leafKey = [RSACng]::new(2048)
$certificates = @()
try {
    $start = [DateTimeOffset]::UtcNow.AddDays(-1)
    $end = [DateTimeOffset]::UtcNow.AddDays(30)
    $rootRequest = New-TestRequest 'CN=Claim Sync Test Root' $rootKey $true
    $root = $rootRequest.CreateSelfSigned($start, $end)
    $certificates += $root
    $intermediateRequest = New-TestRequest 'CN=Claim Sync Test Intermediate' $intermediateKey $true
    $intermediatePublic = $intermediateRequest.Create($root, $start, $end.AddDays(-1), [byte[]]@(1, 2, 3, 4))
    $intermediate = [RSACertificateExtensions]::CopyWithPrivateKey($intermediatePublic, $intermediateKey)
    $certificates += $intermediatePublic, $intermediate
    $leafRequest = New-TestRequest 'CN=localhost' $leafKey $false
    $san = [SubjectAlternativeNameBuilder]::new()
    $san.AddDnsName('localhost')
    $san.AddIpAddress([Net.IPAddress]::Loopback)
    $leafRequest.CertificateExtensions.Add($san.Build())
    $eku = [OidCollection]::new()
    [void]$eku.Add([Oid]::new('1.3.6.1.5.5.7.3.1'))
    $leafRequest.CertificateExtensions.Add([X509EnhancedKeyUsageExtension]::new($eku, $false))
    $leafPublic = $leafRequest.Create($intermediate, $start, $end.AddDays(-2), [byte[]]@(5, 6, 7, 8))
    $leaf = [RSACertificateExtensions]::CopyWithPrivateKey($leafPublic, $leafKey)
    $certificates += $leafPublic, $leaf

    $collection = [X509Certificate2Collection]::new()
    [void]$collection.AddRange([X509Certificate2[]]@($root, $intermediate, $leaf))
    [IO.File]::WriteAllBytes((Join-Path $OutputDirectory 'chain.pfx'), $collection.Export([X509ContentType]::Pfx, 'test-only-password'))
    [IO.File]::WriteAllBytes((Join-Path $OutputDirectory 'leaf-only.pfx'), $leaf.Export([X509ContentType]::Pfx, 'test-only-password'))
    $caOnly = [X509Certificate2Collection]::new()
    [void]$caOnly.Add([X509Certificate2]::new($root.RawData))
    [void]$caOnly.Add([X509Certificate2]::new($intermediate.RawData))
    [IO.File]::WriteAllBytes((Join-Path $OutputDirectory 'public-only.pfx'), $caOnly.Export([X509ContentType]::Pfx, ''))
    foreach ($certificate in $caOnly) { $certificate.Dispose() }

    # Public certificates and a throwaway server key for a Python loopback TLS server.
    # The application export tool never exports a private key.
    $parameters = $leafKey.ExportParameters($true)
    $keyFields = @{}
    foreach ($name in @('Modulus', 'Exponent', 'D', 'P', 'Q', 'DP', 'DQ', 'InverseQ')) {
        $keyFields[$name] = [Convert]::ToBase64String($parameters.$name)
    }
    $fixture = @{
        root = [Convert]::ToBase64String($root.RawData)
        intermediate = [Convert]::ToBase64String($intermediate.RawData)
        leaf = [Convert]::ToBase64String($leaf.RawData)
        key = $keyFields
    }
    [IO.File]::WriteAllText((Join-Path $OutputDirectory 'certificates.json'), ($fixture | ConvertTo-Json), [Text.Encoding]::UTF8)
}
finally {
    foreach ($certificate in $certificates) { $certificate.Dispose() }
    $rootKey.Dispose()
    $intermediateKey.Dispose()
    $leafKey.Dispose()
}
