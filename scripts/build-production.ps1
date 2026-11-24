param()
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$config = Get-Content (Join-Path $root 'packaging\build-config.json') -Raw | ConvertFrom-Json
$version = [string]$config.app_version
$sourceVersion = (Get-Content (Join-Path $root 'src\te\studio\version.py') -Raw)
if ($sourceVersion -notmatch [regex]::Escape('APP_VERSION = "' + $version + '"')) {
    throw 'build-config.json and source version disagree.'
}
. (Join-Path $PSScriptRoot 'production-tools.ps1')
$tools = Find-ProductionTools
$store = Join-Path $root 'packaging\vendor'
& $tools.Python -I (Join-Path $PSScriptRoot 'production_inputs.py') validate --store $store
if ($LASTEXITCODE -ne 0) { throw 'Build inputs are missing or changed. Run prepare-production.ps1.' }
$ffmpeg = Join-Path $store 'ffmpeg.exe'
$ffprobe = Join-Path $store 'ffprobe.exe'
$font = Join-Path $store 'NotoSansCJKjp-Regular.otf'
$publicKey = Join-Path $store 'release-public-key.pem'
$ffmpegNotice = Join-Path $store 'FFmpeg-LICENSE.txt'
$ffmpegReadme = Join-Path $store 'FFmpeg-README.txt'
$fontNotice = Join-Path $store 'OFL.txt'
$ISCC = $tools.ISCC
$python = Join-Path $root '.venv-build\Scripts\python.exe'
# Recreate the disposable environment from the verified Python installation.
& $tools.Python -I -m venv --clear (Join-Path $root '.venv-build')
if ($LASTEXITCODE -ne 0) { throw 'Python 3.12 build environment failed.' }
Push-Location $root
try {
    & $python -m pip install 'PyInstaller==6.16.0' '-e' '.[gui]'
    if ($LASTEXITCODE -ne 0) { throw 'Build dependencies failed.' }
    $entry = Join-Path $root 'packaging\te_studio_entry.py'
    $args = @('--noconfirm','--clean','--onedir','--windowed','--name','TE Studio',
        '--contents-directory','_internal','--collect-data','te',
        '--add-data',"${ffmpeg};resources/bin",
        '--add-data',"${ffprobe};resources/bin",
        '--add-data',"${font};resources/fonts",
        '--add-data',"${ffmpegNotice};resources/licenses",
        '--add-data',"${ffmpegReadme};resources/licenses",
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
