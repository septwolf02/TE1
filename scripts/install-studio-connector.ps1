param(
    [Parameter(Mandatory=$true)][ValidatePattern('^[a-p]{32}$')][string]$ExtensionId,
    [string]$PythonExe = (Join-Path (Split-Path $PSScriptRoot -Parent) '.venv\Scripts\python.exe')
)
$ErrorActionPreference = 'Stop'
if (-not (Test-Path -LiteralPath $PythonExe -PathType Leaf)) { throw "Không tìm thấy Python: $PythonExe" }
$target = Join-Path $env:LOCALAPPDATA 'TEStudio\studio-connector'
New-Item -ItemType Directory -Path $target -Force | Out-Null
$exe = Join-Path $target 'te-studio-native-host.exe'
$manifest = Join-Path $target 'native-host.json'
$PythonExe = (Resolve-Path -LiteralPath $PythonExe).Path
$escapedPython = $PythonExe.Replace('"', '\"')
$source = @"
using System;
using System.Diagnostics;
using System.Threading.Tasks;
public static class TEStudioNativeHost {
    public static int Main(string[] args) {
        if (args.Length < 1 || args[0] != "chrome-extension://$ExtensionId/") return 2;
        var start = new ProcessStartInfo();
        start.FileName = @"$escapedPython";
        start.Arguments = "-m te.studio.studio_connector_host";
        start.UseShellExecute = false;
        start.RedirectStandardInput = true;
        start.RedirectStandardOutput = true;
        start.CreateNoWindow = true;
        using (var child = Process.Start(start)) {
            var input = Task.Run(() => {
                try { Console.OpenStandardInput().CopyTo(child.StandardInput.BaseStream); }
                catch (System.IO.IOException) { }
            });
            child.StandardOutput.BaseStream.CopyTo(Console.OpenStandardOutput());
            child.WaitForExit();
            return child.ExitCode;
        }
    }
}
"@
if (Test-Path -LiteralPath $exe) { Remove-Item -LiteralPath $exe -Force }
Add-Type -TypeDefinition $source -OutputAssembly $exe -OutputType ConsoleApplication
$hostData = @{
    name = 'com.testudio.studio_connector'
    description = 'TE Studio read-only channel discovery prototype'
    path = $exe
    type = 'stdio'
    allowed_origins = @("chrome-extension://$ExtensionId/")
}
[System.IO.File]::WriteAllText($manifest, ($hostData | ConvertTo-Json -Depth 5), (New-Object System.Text.UTF8Encoding($false)))
$registry = 'HKCU:\Software\Google\Chrome\NativeMessagingHosts\com.testudio.studio_connector'
New-Item -Path $registry -Force | Out-Null
Set-Item -Path $registry -Value $manifest
Write-Host "Đã đăng ký Native Messaging host cho extension $ExtensionId."
Write-Host "Manifest: $manifest"
