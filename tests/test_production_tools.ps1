# Run with PowerShell 5.1 or 7. No installed Windows compiler is needed.
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot '../scripts/production-tools.ps1')

$compiler = 'C:\Program Files (x86)\Inno Setup 6\ISCC.exe'
$pyrsys = 'CN=Pyrsys B.V., O=Pyrsys B.V., S=Noord-Holland, C=NL'
$script:probeCalls = 0

function Get-AuthenticodeSignature([string]$LiteralPath) {
    return $script:signature
}

function Invoke-InnoCompilerProbe([string]$Path) {
    $script:probeCalls++
    return $script:banner
}

function Set-CompilerFixture([string]$Status, [string]$Name, [string]$Subject, [string]$Banner) {
    $cert = [pscustomobject]@{ Subject = $Subject; Name = $Name }
    $cert | Add-Member ScriptMethod GetNameInfo { param($NameType, $ForIssuer) return $this.Name }
    $script:signature = [pscustomobject]@{ Status = $Status; SignerCertificate = $cert }
    $script:banner = $Banner
    $script:probeCalls = 0
}

function Assert-Rejected([string]$Expected, [int]$ProbeCalls) {
    $reason = $null
    try { $null = Get-TrustedInnoCompilerVersion $compiler }
    catch { $reason = $_.Exception.Message }
    if ($reason -notlike "*$Expected*" -or $script:probeCalls -ne $ProbeCalls) {
        throw "Expected '$Expected' and $ProbeCalls probe calls. Got '$reason' and $script:probeCalls."
    }
}

# The Windows report has unusable version metadata. Any metadata lookup here is a failure.
function Get-Item([string]$Path) {
    if ($Path -match '^HK(CU|LM):') { return $null }
    throw 'The compiler FileVersion metadata must not be used.'
}

$validBanner = "Inno Setup 6 Command-Line Compiler`r`nCompiler engine version: Inno Setup 6.7.4`r`n"
Set-CompilerFixture 'Valid' 'Pyrsys B.V.' $pyrsys $validBanner
if ((Get-TrustedInnoCompilerVersion $compiler) -ne [version]'6.7.4' -or $script:probeCalls -ne 1) {
    throw 'Rejected the reported Windows Inno Setup installation.'
}

Set-CompilerFixture 'Valid' 'Jordan Russell' 'CN=Jordan Russell' $validBanner
if ((Get-TrustedInnoCompilerVersion $compiler) -ne [version]'6.7.4') {
    throw 'Rejected the existing publisher policy.'
}

foreach ($status in @('NotSigned', 'HashMismatch', 'NotTrusted')) {
    Set-CompilerFixture $status 'Pyrsys B.V.' $pyrsys $validBanner
    Assert-Rejected 'Invalid Authenticode signature' 0
}

Set-CompilerFixture 'Valid' 'Pyrsys B.V. Impostor' 'CN=Pyrsys B.V. Impostor' $validBanner
Assert-Rejected 'Untrusted Inno Setup signer' 0

Set-CompilerFixture 'Valid' 'Pyrsys B.V.' 'CN=Pyrsys B.V., O=Pyrsys B.V., C=US' $validBanner
Assert-Rejected 'Untrusted Inno Setup signer' 0

Set-CompilerFixture 'Valid' 'Pyrsys B.V.' $pyrsys 'Compiler engine version: Inno Setup 7.0.1'
Assert-Rejected 'Incorrect Inno Setup compiler version' 1

Set-CompilerFixture 'Valid' 'Pyrsys B.V.' $pyrsys 'Inno Setup 6 Command-Line Compiler'
Assert-Rejected 'Could not determine Inno Setup compiler engine version' 1

function Test-Path([string]$LiteralPath, [string]$PathType) { return $false }
function Write-Warning([string]$Message) { $script:warning = $Message }
$script:warning = ''
$missing = $false
try { $null = Find-ProductionTools } catch {
    $missing = $_.Exception.Message -like '*trusted Inno Setup 6 is unavailable*'
}
if (!$missing -or $script:warning -notlike 'Missing Inno Setup compiler executable*') {
    throw 'Missing ISCC.exe did not produce its own diagnostic.'
}
Write-Host 'Production tool detection tests passed.'
