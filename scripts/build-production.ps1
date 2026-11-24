param(
    [Parameter(Mandatory=$true)][string]$FFmpegDir,
    [Parameter(Mandatory=$true)][string]$FFmpegSha256,
    [Parameter(Mandatory=$true)][string]$FFprobeSha256,
    [Parameter(Mandatory=$true)][string]$FFmpegLicense,
    [Parameter(Mandatory=$true)][string]$FontFile,
    [Parameter(Mandatory=$true)][string]$FontSha256,
    [Parameter(Mandatory=$true)][string]$FontLicense,
    [Parameter(Mandatory=$true)][string]$SigningPublicKey,
    [Parameter(Mandatory=$true)][string]$SigningKeySha256,
    [string]$ISCC = 'ISCC.exe'
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$config = Get-Content (Join-Path $root 'packaging\build-config.json') -Raw | ConvertFrom-Json
$version = [string]$config.app_version
$sourceVersion = (Get-Content (Join-Path $root 'src\te\studio\version.py') -Raw)
if ($sourceVersion -notmatch [regex]::Escape('APP_VERSION = "' + $version + '"')) {
    throw 'build-config.json and source version disagree.'
}
function Assert-Hash([string]$Path, [string]$Expected) {
    $resolved = (Resolve-Path $Path).Path
    if ($Expected -notmatch '^[0-9a-fA-F]{64}$') { throw "Invalid SHA-256 for $Path" }
    $actual = (Get-FileHash -Algorithm SHA256 $resolved).Hash
    if ($actual -ine $Expected) { throw "SHA-256 mismatch: $Path" }
    return $resolved
}
$bin = (Resolve-Path $FFmpegDir).Path
$ffmpeg = Assert-Hash (Join-Path $bin 'ffmpeg.exe') $FFmpegSha256
$ffprobe = Assert-Hash (Join-Path $bin 'ffprobe.exe') $FFprobeSha256
$font = Assert-Hash $FontFile $FontSha256
$publicKey = Assert-Hash $SigningPublicKey $SigningKeySha256
$ffmpegNotice = (Resolve-Path $FFmpegLicense).Path
$fontNotice = (Resolve-Path $FontLicense).Path
if ([IO.Path]::GetFileName($ffmpegNotice) -ieq [IO.Path]::GetFileName($fontNotice)) {
    throw 'FFmpeg and font notices need distinct filenames.'
}
if ((Get-Item $ffmpegNotice).Length -eq 0 -or (Get-Item $fontNotice).Length -eq 0) {
    throw 'Redistribution license notices must not be empty.'
}
if ([IO.Path]::GetFileName($font) -ne 'NotoSansCJKjp-Regular.otf') {
    throw 'Provide NotoSansCJKjp-Regular.otf with its matching OFL notice.'
}
if ([IO.Path]::GetFileName($publicKey) -ne 'release-public-key.pem') {
    throw 'Name the trusted key release-public-key.pem.'
}
$python = Join-Path $root '.venv-build\Scripts\python.exe'
if (!(Test-Path $python)) {
    & py -3.12 -m venv (Join-Path $root '.venv-build')
    if ($LASTEXITCODE -ne 0) { throw 'Python 3.12 build environment failed.' }
}
Push-Location $root
try {
    & $python -m pip install 'PyInstaller==6.16.0' '-e' '.[gui]'
    if ($LASTEXITCODE -ne 0) { throw 'Build dependencies failed.' }
    $entry = Join-Path $root 'packaging\te_studio_entry.py'
    $args = @('--noconfirm','--clean','--onedir','--windowed','--name','TE Studio',
        '--contents-directory','_internal','--collect-data','te',
        '--add-binary',"${bin};resources/bin",
        '--add-data',"${font};resources/fonts",
        '--add-data',"${ffmpegNotice};resources/licenses",
        '--add-data',"${fontNotice};resources/licenses",
        '--add-data',"${publicKey};resources/release",
        '--distpath',(Join-Path $root 'dist'),
        '--workpath',(Join-Path $root 'build'),
        '--specpath',(Join-Path $root 'build'),$entry)
    & $python -m PyInstaller @args
    if ($LASTEXITCODE -ne 0) { throw 'PyInstaller build failed.' }
    $dist = Join-Path $root 'dist\TE Studio'
    if (!(Test-Path (Join-Path $dist 'TE Studio.exe'))) { throw 'Missing packaged TE Studio.exe.' }
    foreach ($name in @('ffmpeg.exe','ffprobe.exe')) {
        if (!(Test-Path (Join-Path $dist "_internal\resources\bin\$name"))) {
            throw "Missing bundled $name"
        }
    }
    foreach ($resource in @('fonts\NotoSansCJKjp-Regular.otf',
                            'release\release-public-key.pem')) {
        if (!(Test-Path (Join-Path $dist "_internal\resources\$resource"))) {
            throw "Missing bundled $resource"
        }
    }
    $out = Join-Path $root 'dist\installer'
    New-Item -ItemType Directory -Force $out | Out-Null
    & $ISCC "/DAppVersion=$version" "/DSourceDir=$dist" "/DOutputDir=$out" (Join-Path $root 'packaging\TE-Studio.iss')
    if ($LASTEXITCODE -ne 0) { throw 'Inno Setup build failed.' }
    $installer = Join-Path $out "TE-Studio-Setup-$version.exe"
    if (!(Test-Path $installer)) { throw 'Inno Setup did not produce the expected installer.' }
    Write-Host "Installer: $installer"
    Write-Host "SHA-256: $((Get-FileHash -Algorithm SHA256 $installer).Hash)"
} finally { Pop-Location }
