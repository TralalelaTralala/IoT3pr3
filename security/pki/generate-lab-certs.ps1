param(
  [string]$OpenSsl = 'openssl'
)
$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$keys = Join-Path $here '..\..\keys'
$certs = Join-Path $here '..\..\certs'
New-Item -ItemType Directory -Force -Path $keys, $certs | Out-Null
# OpenSSL will prompt for encryption passphrases. Do not use empty passphrases.
& $OpenSsl genpkey -algorithm RSA -aes-256-cbc -pkeyopt rsa_keygen_bits:4096 -out (Join-Path $keys 'root-ca.key.pem')
if ($LASTEXITCODE -ne 0) { throw 'Root key generation failed.' }
& $OpenSsl req -x509 -new -sha256 -days 3650 -key (Join-Path $keys 'root-ca.key.pem') -out (Join-Path $certs 'root-ca.crt.pem') -config (Join-Path $here 'openssl-ca.cnf.example')
if ($LASTEXITCODE -ne 0) { throw 'Root certificate generation failed.' }
Write-Host 'Created encrypted lab root CA key and root certificate.'
Write-Host 'Keep security/keys outside source control. Add intermediate and device enrollment only after reviewing your lab identity policy.'
