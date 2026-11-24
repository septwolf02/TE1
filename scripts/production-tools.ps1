# Discover installed Windows build tools without installing anything.
function Find-ProductionTools {
    $pythonPaths = @(
        "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe"
        "$env:ProgramFiles\Python312\python.exe"
        'C:\Python312\python.exe'
    )
    foreach ($hive in @('HKCU:', 'HKLM:')) {
        $entry = Get-Item "$hive\Software\Python\PythonCore\3.12\InstallPath" -ErrorAction SilentlyContinue
        if ($entry) {
            $exe = $entry.GetValue('ExecutablePath')
            if ($exe) { $pythonPaths += $exe }
            elseif ($entry.GetValue('')) { $pythonPaths += Join-Path ($entry.GetValue('')) 'python.exe' }
        }
    }
    $pythonPaths += @(Get-Command python.exe -CommandType Application -All -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty Source)
    $python = $null
    foreach ($path in $pythonPaths | Select-Object -Unique) {
        if (!(Test-Path -LiteralPath $path -PathType Leaf)) { continue }
        $signature = Get-AuthenticodeSignature -LiteralPath $path
        if ($signature.Status -ne 'Valid' -or
            $signature.SignerCertificate.Subject -notmatch 'Python Software Foundation') { continue }
        $version = & $path -I -c "import sys,sysconfig,tkinter,venv; print(str(sys.version_info.major)+'.'+str(sys.version_info.minor)+' '+sysconfig.get_platform())"
        if ($LASTEXITCODE -eq 0 -and $version -eq '3.12 win-amd64') { $python = $path; break }
    }

    $isccPaths = @(
        "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe"
        "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
        "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"
    )
    $isccPaths += @(Get-Command ISCC.exe -CommandType Application -All -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty Source)
    $iscc = $null
    foreach ($path in $isccPaths | Select-Object -Unique) {
        if (!(Test-Path -LiteralPath $path -PathType Leaf)) { continue }
        $info = (Get-Item -LiteralPath $path).VersionInfo
        $signature = Get-AuthenticodeSignature -LiteralPath $path
        if ($info.FileMajorPart -eq 6 -and $signature.Status -eq 'Valid' -and
            $signature.SignerCertificate.Subject -match 'Jordan Russell|Martijn Laan') {
            $iscc = $path
            break
        }
    }
    if (!$python) { Write-Host 'Install official Python 3.12 Windows x64: https://www.python.org/downloads/windows/' }
    if (!$iscc) { Write-Host 'Install official signed Inno Setup 6: https://jrsoftware.org/isdl.php' }
    if (!$python -or !$iscc) {
        throw 'Required signed x64 Python 3.12 or Inno Setup 6 was not found. Review and install the missing tools yourself, then retry.'
    }
    return @{ Python = $python; ISCC = $iscc }
}
