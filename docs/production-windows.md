# TE Studio Production V1 — Windows

## Trạng thái

`te-v1-render-test` hiện chỉ chứa hạ tầng build cho **release candidate 1.0.0rc1**. Chưa có installer đã kiểm thử trên Windows và chưa có Production Release. Không công bố bản ổn định trước khi hoàn thành kiểm tra Windows bên dưới.

Production cài tại `%LOCALAPPDATA%\Programs\TE Studio`, độc lập với `D:\TE-Git` và `.venv`. Development tiếp tục dùng `D:\TE-Git\.venv\Scripts\python.exe -m te.studio`. Production ghi settings/queue riêng tại `%LOCALAPPDATA%\TEStudio\production`; lần đầu chỉ đọc và sao các tùy chọn tương thích từ `%LOCALAPPDATA%\TEStudio\settings.json`, không sửa file Development. Cả hai có thể mở dự án tại `D:\TE-Projects`; khóa `.studio-edit.lock` ngăn hai cửa sổ chỉnh sửa cùng một dự án. Installer không sao chép hay xóa media/project khi nâng cấp hoặc uninstall. Nếu bản tương lai đổi schema dự án, cần quy trình sao lưu/migration trước khi dùng chung.

Publisher/Studio Connector đang thử nghiệm nên không có lối vào từ giao diện Production. Development giữ Publisher hiện có. Native Host hiện trỏ tới Python `.venv` và **không** được installer Production âm thầm đăng ký lại. Trước khi bật Publisher trên gói Production, cần một Native Host đóng gói và kiểm thử riêng.

## Nguồn tài nguyên và build

Máy **build** cần Python 3.12, PowerShell và Inno Setup 6 (`ISCC.exe`). Máy **cài** không cần Python, Git hoặc FFmpeg trong PATH. Người build cung cấp:

- Thư mục `bin` của một FFmpeg Windows tương thích có `ffmpeg.exe`, `ffprobe.exe` và DLL đi kèm, cùng license/notices của chính bản build đó. Kiểm tra quyền phân phối và codec. NVENC vẫn cần GPU/driver tương thích; CPU fallback giữ nguyên.
- `NotoSansCJKjp-Regular.otf` có quyền phân phối và OFL notice tương ứng. Không phân phối font lấy từ `C:\Windows\Fonts`.
- `release-public-key.pem` Ed25519 được chủ dự án tin cậy. Khóa riêng ở ngoài repository và tốt nhất giữ offline. Xác minh fingerprint public key trước khi build; đây là gốc tin cậy của Production updates.

Script so SHA-256 do người build cung cấp cho hai EXE, font và public key, rồi đóng gói bằng PyInstaller `onedir`. Các notices vào `resources/licenses`. Tcl/Tk và Python dependencies đi cùng EXE. **Chỉ build và chạy trên Windows sạch mới xác nhận** runtime, FFmpeg DLL, font/libass và codec hoạt động.

Sau cài đặt, giấy phép/phần ghi công nằm tại `%LOCALAPPDATA%\Programs\TE Studio\_internal\resources\licenses`. Người duyệt release phải kiểm tra nội dung notices tương ứng chính xác với FFmpeg binary và font đã đưa vào gói.

```powershell
cd D:\TE-Git
git fetch origin
git switch te-v1-render-test
git pull --ff-only origin te-v1-render-test

$bin = 'D:\BuildInputs\ffmpeg\bin'
$font = 'D:\BuildInputs\NotoSansCJKjp-Regular.otf'
$key = 'D:\BuildInputs\release-public-key.pem'
Get-FileHash -Algorithm SHA256 "$bin\ffmpeg.exe", "$bin\ffprobe.exe", $font, $key

.\scripts\build-production.ps1 `
  -FFmpegDir $bin `
  -FFmpegSha256 '<64 hex đã kiểm chứng>' `
  -FFprobeSha256 '<64 hex đã kiểm chứng>' `
  -FFmpegLicense 'D:\BuildInputs\ffmpeg\LICENSE.txt' `
  -FontFile $font `
  -FontSha256 '<64 hex đã kiểm chứng>' `
  -FontLicense 'D:\BuildInputs\OFL.txt' `
  -SigningPublicKey $key `
  -SigningKeySha256 '<64 hex public key được phê duyệt>' `
  -ISCC 'C:\Program Files (x86)\Inno Setup 6\ISCC.exe'
```

Script tạo `.venv-build` **chỉ trên máy build**. Đầu ra dự kiến: `D:\TE-Git\dist\installer\TE-Studio-Setup-1.0.0rc1.exe`. Không commit `dist`, `build`, `.venv-build`, khóa riêng, binaries hoặc font. `packaging/build-config.json` và `src/te/studio/version.py` phải cùng version.

## Kiểm thử bản cài thử trên Windows sạch

1. Chạy installer bằng tài khoản Windows thường. Kiểm tra Desktop/Start Menu shortcut. Đóng IDE/Python, tạm ngắt `D:\TE-Git`, rồi mở TE Studio bằng shortcut. Nếu không mở, xem `%LOCALAPPDATA%\TEStudio\diagnostics\startup.log`.
2. Tạo dự án thử; mở và lưu dự án cũ ở `D:\TE-Projects`; kiểm tra media ngoài project và settings sau restart. Thử mở cùng dự án ở Development và Production: cửa sổ thứ hai phải từ chối chỉnh sửa.
3. Nếu VOICEVOX chưa có, nút **Cài VOICEVOX** mở trang chính thức để người dùng tự cài. Chạy Engine, bấm **Dò lại** và **Nghe thử**. Xác nhận narration tiếng Nhật và WAV cache.
4. Xuất Full HD MP4 với chữ Nhật, KEY, Intro và media ngoài thư mục cài. Kiểm tra subtitle/font thật, âm thanh, Render Queue, CPU fallback và NVENC trên máy có GPU/driver tương thích.
5. Bấm **Kiểm tra bản cập nhật**; khi chưa có release Production đã ký, không được đề nghị Development, draft hoặc prerelease. Chỉ tải sau khi người dùng chọn **Tải và cài đặt**.
6. Thử nâng cấp rồi uninstall; project, media, voice preset và settings phải còn. Nếu một bước lỗi, giữ release candidate, không ký/publish Production.

## VOICEVOX

First-run Production dò Engine cục bộ ở `127.0.0.1:50021`. Khi thiếu, **Cài VOICEVOX** mở `https://voicevox.hiroshiba.jp/` sau khi người dùng bấm. Đây là **hướng dẫn cài**, chưa phải auto-download có xác minh chữ ký. Người dùng cài/khởi động Engine, bấm **Dò lại** và **Nghe thử**. TE không âm thầm tải hoặc chạy binary ngoài.

## Ký và cập nhật Production

Chỉ sau khi kiểm thử Windows đạt, tăng `APP_VERSION` và `build-config.json` lên một version ổn định như `1.0.0`, build lại và kiểm thử đúng installer đó. Chủ dự án tạo khóa Ed25519 riêng ở máy kiểm soát riêng, không commit hoặc đưa vào log CI:

```powershell
openssl genpkey -algorithm Ed25519 -out D:\Private\te-release-private.pem
openssl pkey -in D:\Private\te-release-private.pem -pubout -out D:\BuildInputs\release-public-key.pem
```

Sau khi chốt installer và release notes, chạy `scripts/sign-production-release.py --installer <exe> --version 1.0.0 --notes <notes.txt> --private-key <private.pem> --output <folder>` bằng Python build. Script chỉ tạo `release.json` và `release.json.sig`, **không upload/publish**. Sau phê duyệt thủ công, GitHub Release phải không phải draft/prerelease, tag `v<version>`, có installer và hai file ký.

Nút **Kiểm tra bản cập nhật** chỉ hiện trong gói Production. TE bỏ draft/prerelease, xác minh Ed25519 bằng public key đi cùng app, kiểm tra `release_kind=production`, tag/asset/version và hiển thị notes/kích thước. Sau khi người dùng đồng ý, installer được tải streaming vào `%LOCALAPPDATA%\TEStudio\updates`; TE kiểm tra byte count và SHA-256 **trong manifest đã ký** trước khi mở installer và thoát. Inno Setup chờ/đóng ứng dụng trước khi thay file. Lỗi tải/kiểm tra xóa file `.part`, giữ bản đang cài. SHA-256 tự công bố trên Release không đủ xác thực nếu thiếu chữ ký từ khóa tin cậy.
