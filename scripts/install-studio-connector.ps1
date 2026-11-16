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
using System.IO;
using System.Threading.Tasks;
public static class TEStudioNativeHost {
    private static byte[] ReadExact(Stream input, int length) {
        var data = new byte[length];
        int offset = 0;
        while (offset < length) {
            int count = input.Read(data, offset, length - offset);
            if (count <= 0) throw new EndOfStreamException();
            offset += count;
        }
        return data;
    }
    private static void ForwardFrames(Stream input, Stream output) {
        while (true) {
            int first = input.ReadByte();
            if (first < 0) return;
            var tail = ReadExact(input, 3);
            var header = new byte[] { (byte)first, tail[0], tail[1], tail[2] };
            uint length = BitConverter.ToUInt32(header, 0);
            if (length > 1048576) throw new InvalidDataException("response_oversize");
            var payload = ReadExact(input, (int)length);
            output.Write(header, 0, 4);
            output.Write(payload, 0, payload.Length);
            output.Flush();
        }
    }
    private static void Log(string stage, string code) {
        try {
            var path = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                "TEStudio", "studio-connector", "connector.log");
            File.AppendAllText(path, DateTime.UtcNow.ToString("o") + " launcher " + stage + " " + code + Environment.NewLine);
        } catch (IOException) { }
    }
    public static int Main(string[] args) {
        if (args.Length < 1 || args[0] != "chrome-extension://$ExtensionId/") {
            Log("origin", "rejected");
            return 2;
        }
        try {
            var frameTask = Task.Run(() => {
                var input = Console.OpenStandardInput();
                var header = ReadExact(input, 4);
                uint length = BitConverter.ToUInt32(header, 0);
                if (length > 1048576) throw new InvalidDataException("oversize");
                return new byte[][] { header, ReadExact(input, (int)length) };
            });
            if (!frameTask.Wait(10000)) throw new TimeoutException();
            var header = frameTask.Result[0];
            var payload = frameTask.Result[1];
            var start = new ProcessStartInfo();
            start.FileName = @"$escapedPython";
            start.Arguments = "-m te.studio.studio_connector_host";
            start.UseShellExecute = false;
            start.RedirectStandardInput = true;
            start.RedirectStandardOutput = true;
            start.CreateNoWindow = true;
            using (var child = Process.Start(start)) {
                if (child == null) throw new InvalidOperationException("python_start_failed");
                child.StandardInput.BaseStream.Write(header, 0, 4);
                child.StandardInput.BaseStream.Write(payload, 0, payload.Length);
                child.StandardInput.BaseStream.Flush();
                child.StandardInput.Close();
                var output = Task.Run(() => ForwardFrames(child.StandardOutput.BaseStream, Console.OpenStandardOutput()));
                if (!output.Wait(10000)) { child.Kill(); throw new TimeoutException(); }
                if (!child.WaitForExit(1000)) { child.Kill(); throw new TimeoutException(); }
                if (child.ExitCode != 0) Log("python", "nonzero_exit");
                return child.ExitCode;
            }
        } catch (Exception error) {
            Log("bridge", error.GetType().Name);
            return 3;
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
