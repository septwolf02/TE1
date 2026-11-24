# Pass -CompilerPath on Windows to also exercise the signed ISCC installed there.
param([string]$CompilerPath)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot '../scripts/production-tools.ps1')

function Assert-EmptyTemp {
    if (@(Get-ChildItem -LiteralPath $env:TEMP -Force).Count -ne 0) {
        throw 'The compiler probe left temporary files behind.'
    }
}

$originalTemp = $env:TEMP
$root = Join-Path ([IO.Path]::GetTempPath()) "TE Studio test $([guid]::NewGuid())"
try {
    $env:TEMP = Join-Path $root 'User Temp With Spaces'
    $null = [IO.Directory]::CreateDirectory($env:TEMP)

    if ($CompilerPath) {
        $version = Get-TrustedInnoCompilerVersion (Resolve-Path -LiteralPath $CompilerPath).Path
        if ($version.Major -ne 6) { throw "Unexpected compiler version: $version" }
        Assert-EmptyTemp
        Write-Host "Signed Windows ISCC probe passed: $version"
    }

    $script:runCount = 0
    $script:compilerError = $false
    function Invoke-InnoProbeProcess([Diagnostics.ProcessStartInfo]$Start) {
        $script:runCount++
        if ($Start.FileName -ne $compiler -or $Start.RedirectStandardInput -or
            !$Start.RedirectStandardOutput -or !$Start.RedirectStandardError) {
            throw 'Compiler path or streams were configured incorrectly.'
        }
        if ($Start.Arguments -notmatch '^/O- "([^"]+\.iss)"$') {
            throw 'Expected a quoted .iss path and disabled compiler output.'
        }
        $iss = $Matches[1]
        $parent = Split-Path -Parent $iss
        if ((Split-Path -Parent $parent) -ne $env:TEMP -or $Start.WorkingDirectory -ne $parent) {
            throw 'Probe script and working directory must live below TEMP.'
        }
        $source = [IO.File]::ReadAllText($iss)
        if ($source -notmatch '(?m)^\[Setup\]\r?$' -or $source -notmatch 'DefaultDirName=') {
            throw 'Expected a readable, valid probe script.'
        }
        [IO.File]::WriteAllText((Join-Path $parent 'probe artifact.txt'), 'cleanup check')
        if ($script:compilerError) { throw 'Compiler exited with code 2. File access denied.' }
        return 'Compiler engine version: Inno Setup 6.7.4'
    }

    $compiler = 'C:\Program Files (x86)\Inno Setup 6\ISCC.exe'
    if ((Invoke-InnoCompilerProbe $compiler) -ne 'Compiler engine version: Inno Setup 6.7.4') {
        throw 'The compiler banner was not returned.'
    }
    Assert-EmptyTemp

    $script:compilerError = $true
    $message = ''
    try { $null = Invoke-InnoCompilerProbe $compiler } catch { $message = $_.Exception.Message }
    if ($message -notlike '*temporary script*code 2*File access denied*') {
        throw "Expected compiler failure with exit code and probe context, got: $message"
    }
    Assert-EmptyTemp

    # Use a regular file as TEMP to force a deterministic write failure without changing ACLs.
    $env:TEMP = Join-Path $root 'Unwritable Temp'
    [IO.File]::WriteAllText($env:TEMP, 'Not a directory')
    $message = ''
    try { $null = Invoke-InnoCompilerProbe $compiler } catch { $message = $_.Exception.Message }
    if ($message -notlike '*Check that TEMP is writable*' -or $script:runCount -ne 2) {
        throw "Expected a TEMP write diagnostic before launching ISCC, got: $message"
    }
    if ([IO.File]::ReadAllText($env:TEMP) -ne 'Not a directory') {
        throw 'Cleanup modified the inaccessible TEMP fixture.'
    }
    Write-Host 'Inno Setup temporary-file probe tests passed.'
} finally {
    $env:TEMP = $originalTemp
    if (Test-Path -LiteralPath $root) {
        Remove-Item -LiteralPath $root -Recurse -Force
    }
}
