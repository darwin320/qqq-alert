# Builds a CA bundle that Python can use on this Windows machine.
#
# Why: the antivirus (Norton) terminates TLS locally and re-signs traffic with
# its own root. That root lives in the Windows certificate store, which Python
# does not read - certifi and curl_cffi ship their own bundle - so every HTTPS
# call fails with "unable to get local issuer certificate".
#
# This merges certifi's bundle with the Windows root store and writes the
# result next to this script. monitor.py and analyze.py pick it up
# automatically on Windows. It is gitignored on purpose: CI runs on Linux with
# no interception and must keep using the stock bundle.
#
#   powershell -File tools\make_ca_bundle.ps1

$ErrorActionPreference = 'Stop'

$certifi = python -c "import certifi;print(certifi.where())"
if (-not (Test-Path $certifi)) { throw "certifi bundle not found at '$certifi'" }

$out = Join-Path $PSScriptRoot 'ca-bundle-local.pem'
$sb  = New-Object System.Text.StringBuilder
[void]$sb.AppendLine((Get-Content $certifi -Raw))

$added = 0
foreach ($store in @('Cert:\LocalMachine\Root', 'Cert:\CurrentUser\Root')) {
    foreach ($cert in (Get-ChildItem $store -ErrorAction SilentlyContinue)) {
        $b64 = [Convert]::ToBase64String($cert.RawData, 'InsertLineBreaks')
        [void]$sb.AppendLine("# $($cert.Subject)")
        [void]$sb.AppendLine('-----BEGIN CERTIFICATE-----')
        [void]$sb.AppendLine($b64)
        [void]$sb.AppendLine('-----END CERTIFICATE-----')
        $added++
    }
}

Set-Content -Path $out -Value $sb.ToString() -Encoding ascii
Write-Host "Wrote $out ($added certificates from the Windows root store)"
