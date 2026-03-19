$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$cert = Join-Path $root 'certs\192.168.50.242+2.pem'
$key  = Join-Path $root 'certs\192.168.50.242+2-key.pem'

if (!(Test-Path $cert) -or !(Test-Path $key)) {
  Write-Host ('Missing cert files: ' + $cert + ' or ' + $key) -ForegroundColor Red
  Write-Host 'Please generate certs with mkcert first.' -ForegroundColor Yellow
  exit 1
}

$env:FRIDGE_SSL_CERT = $cert
$env:FRIDGE_SSL_KEY = $key

Write-Host 'Starting HTTPS server...'
Write-Host 'URL: https://192.168.50.242:5443/' -ForegroundColor Green
Write-Host ''

& (Join-Path $root '.venv\Scripts\python.exe') (Join-Path $root 'app.py')

