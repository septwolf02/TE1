# Discover installed Windows build tools without installing anything.
function Invoke-InnoProbeProcess([Diagnostics.ProcessStartInfo]$Start) {
    $process = [Diagnostics.Process]::Start($start)
    try {
        $output = $process.StandardOutput.ReadToEndAsync()
        $errors = $process.StandardError.ReadToEndAsync()
        if (!$process.WaitForExit(20000)) {
            throw 'Compiler did not exit within 20 seconds.'
        }
        if ($process.ExitCode -ne 0) {
            $detail = $errors.Result.Trim()
            if (!$detail) { $detail = $output.Result.Trim() }
            throw "Compiler exited with code $($process.ExitCode). $detail"
        }
        return $output.Result
    } finally {
        try {
            if (!$process.HasExited) {
                $process.Kill()
                $null = $process.WaitForExit(5000)
            }
        } finally {
            $process.Dispose()
        }
    }
}

function Invoke-InnoCompilerProbe([string]$Path) {
    $tempRoot = $env:TEMP
    if (!$tempRoot) { $tempRoot = [IO.Path]::GetTempPath() }
    $created = $false
    try {
        try {
            $probeDirectory = Join-Path $tempRoot "TE Studio ISCC Probe $([guid]::NewGuid())" -ErrorAction Stop
            $null = [IO.Directory]::CreateDirectory($probeDirectory)
            $created = $true
            $probeScript = Join-Path $probeDirectory 'version probe.iss'
            [IO.File]::WriteAllText($probeScript,
                "[Setup]`r`nAppName=TE Studio Probe`r`nAppVersion=1.0`r`nDefaultDirName={autopf}\TEStudioProbe`r`n",
                [Text.Encoding]::ASCII)
        } catch {
            throw "Cannot write Inno Setup probe under TEMP '$tempRoot'. Check that TEMP is writable. $($_.Exception.Message)"
        }

        # Use a quoted file argument for both Windows PowerShell 5.1 and PowerShell 7.
        $start = [Diagnostics.ProcessStartInfo]::new($Path, ('/O- "{0}"' -f $probeScript))
        $start.WorkingDirectory = $probeDirectory
        $start.UseShellExecute = $false
        $start.CreateNoWindow = $true
        $start.RedirectStandardOutput = $true
        $start.RedirectStandardError = $true
        try {
            Invoke-InnoProbeProcess $start
        } catch {
            throw "Inno Setup version probe failed for '$Path' with temporary script '$probeScript'. $($_.Exception.Message)"
        }
    } finally {
        if ($created) {
            try {
                Remove-Item -LiteralPath $probeDirectory -Recurse -Force -ErrorAction Stop
            } catch {
                Write-Warning "Could not remove Inno Setup probe directory '${probeDirectory}': $($_.Exception.Message)"
            }
        }
    }
}

function Get-TrustedInnoCompilerVersion([string]$Path) {
    $signature = Get-AuthenticodeSignature -LiteralPath $Path
    if ($signature.Status -ne 'Valid' -or !$signature.SignerCertificate) {
        throw "Invalid Authenticode signature ($($signature.Status))."
    }
    $certificate = $signature.SignerCertificate
    $publisher = $certificate.GetNameInfo([Security.Cryptography.X509Certificates.X509NameType]::SimpleName, $false)
    # The official is-6_7_4 release installer is signed by Pyrsys B.V.
    # https://github.com/jrsoftware/issrc/releases/tag/is-6_7_4
    $approvedPyrsys = $publisher -eq 'Pyrsys B.V.' -and
        $certificate.Subject -match '(?:^|,\s*)O=Pyrsys B\.V\.(?:,|$)' -and
        $certificate.Subject -match '(?:^|,\s*)C=NL(?:,|$)'
    if (!$approvedPyrsys -and $publisher -notin @('Jordan Russell', 'Martijn Laan')) {
        throw "Untrusted Inno Setup signer: $($certificate.Subject)"
    }

    $banner = Invoke-InnoCompilerProbe $Path
    if ($banner -notmatch '(?m)^Compiler engine version: Inno Setup (?<version>\d+(?:\.\d+){1,3})(?:[ \t]+[^\r\n]*)?\r?$') {
        throw 'Could not determine Inno Setup compiler engine version.'
    }
    $version = [version]$Matches.version
    if ($version.Major -ne 6) {
        throw "Incorrect Inno Setup compiler version: $version (expected 6.x)."
    }
    return $version
}

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
    $foundCompiler = $false
    foreach ($path in $isccPaths | Select-Object -Unique) {
        if (!(Test-Path -LiteralPath $path -PathType Leaf)) { continue }
        $foundCompiler = $true
        try {
            $compilerVersion = Get-TrustedInnoCompilerVersion $path
            Write-Host "Verified Inno Setup compiler $compilerVersion at $path"
            $iscc = $path
            break
        } catch {
            Write-Warning "Rejected Inno Setup compiler '${path}': $($_.Exception.Message)"
        }
    }
    if (!$foundCompiler) { Write-Warning 'Missing Inno Setup compiler executable (ISCC.exe).' }
    if (!$python) { Write-Host 'Install official Python 3.12 Windows x64: https://www.python.org/downloads/windows/' }
    if (!$iscc) { Write-Host 'Install official signed Inno Setup 6: https://jrsoftware.org/isdl.php' }
    if (!$python -or !$iscc) {
        throw 'Required signed x64 Python 3.12 or trusted Inno Setup 6 is unavailable. See the diagnostics above.'
    }
    return @{ Python = $python; ISCC = $iscc }
}
