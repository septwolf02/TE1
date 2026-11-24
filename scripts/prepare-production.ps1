param(
    [string]$PublicKey = (Join-Path $env:LOCALAPPDATA 'TEStudio\release-signing\release-public-key.pem'),
    [string[]]$InputDirectory = @()
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot 'production-tools.ps1')
$tools = Find-ProductionTools
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$helper = Join-Path $PSScriptRoot 'production_inputs.py'
$store = Join-Path $root 'packaging\vendor'

if (!(Test-Path -LiteralPath $PublicKey -PathType Leaf)) {
    throw 'Missing owner public key. Follow docs/production-windows.md to initialize signing keys.'
}
$fingerprint = & $tools.Python -I $helper fingerprint --key $PublicKey
if ($LASTEXITCODE -ne 0) { throw 'Public-key validation failed.' }
Write-Host "Python: $($tools.Python)"
Write-Host "Inno Setup: $($tools.ISCC)"
Write-Host 'FFmpeg: Gyan 8.0.1 essentials (GPLv3); https://ffmpeg.org/download.html#build-windows'
Write-Host 'Font: Noto Sans CJK JP Sans2.004 (OFL 1.1); https://github.com/notofonts/noto-cjk'
Write-Host "Ed25519 public key: $PublicKey"
Write-Host "SHA-256 fingerprint (DER): $fingerprint"
Write-Host 'Compare this fingerprint with the one reviewed by the key owner.'
if ((Read-Host 'Type APPROVE to approve this key, distributions, licenses and any required asset downloads') -cne 'APPROVE') {
    throw 'Preparation cancelled.'
}

$arguments = @('prepare', '--store', $store, '--key', $PublicKey,
               '--fingerprint', $fingerprint, '--download')
$directories = $InputDirectory + @(
    (Join-Path $env:LOCALAPPDATA 'Microsoft\Windows\Fonts')
    (Join-Path $env:WINDIR 'Fonts')
)
foreach ($name in @('ffmpeg.exe', 'ffprobe.exe')) {
    $directories += @(Get-Command $name -CommandType Application -All -ErrorAction SilentlyContinue |
        ForEach-Object { Split-Path -Parent $_.Source })
}
foreach ($directory in $directories | Select-Object -Unique) {
    $arguments += @('--candidate', $directory)
}
& $tools.Python -I $helper @arguments
if ($LASTEXITCODE -ne 0) { throw 'Build input preparation failed.' }
Write-Host "Prepared and verified: $store\build.json"
Write-Host 'Run .\scripts\build-production.ps1 to build the release candidate.'
