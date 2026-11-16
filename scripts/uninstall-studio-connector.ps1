$ErrorActionPreference = 'Stop'
$registry = 'HKCU:\Software\Google\Chrome\NativeMessagingHosts\com.testudio.studio_connector'
if (Test-Path $registry) { Remove-Item -Path $registry -Force }
$target = Join-Path $env:LOCALAPPDATA 'TEStudio\studio-connector'
foreach ($name in @('native-host.json', 'te-studio-native-host.exe')) {
    $path = Join-Path $target $name
    if (Test-Path -LiteralPath $path) { Remove-Item -LiteralPath $path -Force }
}
Write-Host 'Đã gỡ đăng ký Native Messaging host. Bản quét scan.json vẫn được giữ; có thể xóa riêng nếu muốn.'
