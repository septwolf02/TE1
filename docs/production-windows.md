# TE Studio Production V1 — Windows

## Trạng thái

`te-v1-render-test` hiện chỉ chứa hạ tầng build cho **release candidate 1.0.0rc1**. Chưa có installer đã kiểm thử trên Windows và chưa có Production Release. Không công bố bản ổn định trước khi hoàn thành kiểm tra Windows bên dưới.

Production cài tại `%LOCALAPPDATA%\Programs\TE Studio`, độc lập với `D:\TE-Git` và `.venv`. Development tiếp tục dùng `D:\TE-Git\.venv\Scripts\python.exe -m te.studio`. Production ghi settings/queue riêng tại `%LOCALAPPDATA%\TEStudio\production`; lần đầu chỉ đọc và sao các tùy chọn tương thích từ `%LOCALAPPDATA%\TEStudio\settings.json`, không sửa file Development. Cả hai có thể mở dự án tại `D:\TE-Projects`; khóa `.studio-edit.lock` ngăn hai cửa sổ chỉnh sửa cùng một dự án. Installer không sao chép hay xóa media/project khi nâng cấp hoặc uninstall. Nếu bản tương lai đổi schema dự án, cần quy trình sao lưu/migration trước khi dùng chung.

Publisher/Studio Connector đang thử nghiệm nên không có lối vào từ giao diện Production. Development giữ Publisher hiện có. Native Host hiện trỏ tới Python `.venv` và **không** được installer Production âm thầm đăng ký lại. Trước khi bật Publisher trên gói Production, cần một Native Host đóng gói và kiểm thử riêng.

## Chuẩn bị và build trên Windows

Máy build cần Python 3.12 x64 có Tcl/Tk và Inno Setup 6. Script dò các vị trí cài phổ biến, Python registry và PATH, kiểm tra phiên bản và chữ ký Authenticode của nhà phát hành. Nếu thiếu, xem [Python Windows](https://www.python.org/downloads/windows/) và [Inno Setup](https://jrsoftware.org/isdl.php), kiểm tra nhà phát hành và tự quyết định cài. Script không tự cài các công cụ hệ thống. Lệnh build cài PyInstaller và các gói Python cần thiết vào `.venv-build` dùng riêng để build.

Chủ dự án tạo khóa Ed25519 một lần trên máy giữ khóa, với OpenSSL đã có từ nguồn tin cậy. Tạo trong thư mục riêng và giữ file private ở ngoài Git. Các lệnh này dành cho **lần khởi tạo duy nhất**.

```powershell
$signing = Join-Path $env:LOCALAPPDATA 'TEStudio\release-signing'
New-Item -ItemType Directory -Force $signing | Out-Null
$private = Join-Path $signing 'release-private-key.pem'
$public = Join-Path $signing 'release-public-key.pem'
if ((Test-Path $private) -or (Test-Path $public)) { throw 'Signing keys already exist.' }
openssl genpkey -algorithm Ed25519 -out $private
if ($LASTEXITCODE -ne 0) { throw 'Key generation failed.' }
openssl pkey -in $private -pubout -out $public
if ($LASTEXITCODE -ne 0) { throw 'Public key export failed.' }
openssl pkey -pubin -in $public -outform DER -out (Join-Path $signing 'release-public-key.der')
if ($LASTEXITCODE -ne 0) { throw 'Public DER export failed.' }
Get-FileHash -Algorithm SHA256 (Join-Path $signing 'release-public-key.der')
```

Lưu bản sao khóa riêng có kiểm soát và giữ offline. Chuyển **public PEM và fingerprint DER** cho máy build qua kênh tin cậy. Máy build chỉ cần public PEM tại đường dẫn mặc định `%LOCALAPPDATA%\TEStudio\release-signing\release-public-key.pem` hoặc đường dẫn ngoài repo truyền qua `-PublicKey`. Nếu cần OpenSSL, xem [hướng dẫn của dự án OpenSSL](https://openssl-library.org/source/) hoặc nguồn phần mềm đã được tổ chức phê duyệt.

```powershell
cd D:\TE-Git
git switch te-v1-render-test
git pull --ff-only origin te-v1-render-test
.\scripts\prepare-production.ps1
.\scripts\build-production.ps1
```

Ở bước chuẩn bị, đối chiếu fingerprint hiển thị với fingerprint chủ dự án gửi. Xem nguồn và giấy phép, rồi nhập `APPROVE` nếu đồng ý. Script dùng lại các bản trùng SHA-256 đã ghim trong cache, PATH, thư mục font Windows hoặc thư mục bổ sung truyền bằng `-InputDirectory`. Nếu cần, script tải **Gyan FFmpeg Windows x64 8.0.1 essentials** (GPLv3) từ [GyanD/codexffmpeg](https://github.com/GyanD/codexffmpeg/releases/tag/8.0.1), được [FFmpeg dẫn link](https://ffmpeg.org/download.html#build-windows), và **Noto Sans CJK JP Sans2.004** (OFL 1.1) từ [Noto CJK](https://github.com/notofonts/noto-cjk/tree/Sans2.004). Tệp có hash không khớp ở cache làm bước chuẩn bị dừng. Kiểm tra tệp báo lỗi và xóa đúng tệp cache đó để tải lại từ nguồn đã ghim.

Script kiểm tra cả kho lưu trữ FFmpeg và từng tệp được dùng, cùng font và hai bộ giấy phép. Cấu hình local `packaging/vendor/build.json` lưu hash; build kiểm tra lại các tệp theo hash đã ghim trong mã nguồn và fingerprint đã phê duyệt trước khi đóng gói. Installer chỉ đưa hai EXE FFmpeg đã chọn, README của nhà phân phối và giấy phép tương ứng. Trước khi phân phối, chủ dự án cần đáp ứng nghĩa vụ cung cấp mã nguồn tương ứng cho bản FFmpeg GPLv3 và các thành phần đi kèm; README có thông tin cấu hình và mốc mã nguồn của nhà cung cấp. Font giữ nguyên OFL.

Đầu ra dự kiến là `D:\TE-Git\dist\installer\TE-Studio-Setup-1.0.0rc1.exe`. Sau khi build, chạy installer bằng tài khoản Windows thường và mở shortcut **TE Studio**. Máy cài không cần Python, Git hay FFmpeg trong PATH. Thư mục build, cấu hình local, khóa và tệp tải về không đưa lên Git.

## Nhận diện ứng dụng

Biểu tượng TE và timeline hiện là mẫu tạm do dự án tạo. Khi logo chính thức được duyệt, xuất thành ICO có các kích thước 16, 32, 48, 64, 128 và 256 px rồi thay `src/te/branding/te-studio.ico` và build lại. Cùng một tệp được nhúng vào EXE và installer, dùng cho shortcut và đóng gói làm tài nguyên cho cửa sổ, hộp thoại Tk và mục **Trợ giúp → Giới thiệu TE Studio**. Hộp thoại do Windows quản lý có thể dùng biểu tượng hệ thống.

Trong lần kiểm thử Windows, so biểu tượng của installer, EXE, shortcut Desktop, Start Menu, Taskbar, cửa sổ và hộp thoại **Giới thiệu**. Mục **Giới thiệu** phải hiển thị đúng phiên bản trên bộ cài.

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

Chỉ sau khi kiểm thử Windows đạt, tăng `APP_VERSION` và `build-config.json` lên một version ổn định như `1.0.0`, build lại và kiểm thử đúng installer đó. Dùng cặp khóa do chủ dự án khởi tạo ở trên. Installer RC chỉ nhúng khóa công khai để kiểm tra cập nhật.

Sau khi chốt installer và release notes, chạy `scripts/sign-production-release.py --installer <exe> --version 1.0.0 --notes <notes.txt> --private-key <private.pem> --output <folder>` bằng Python build. Script chỉ tạo `release.json` và `release.json.sig`, **không upload/publish**. Sau phê duyệt thủ công, GitHub Release phải không phải draft/prerelease, tag `v<version>`, có installer và hai file ký.

Nút **Kiểm tra bản cập nhật** chỉ hiện trong gói Production. TE bỏ draft/prerelease, xác minh Ed25519 bằng public key đi cùng app, kiểm tra `release_kind=production`, tag/asset/version và hiển thị notes/kích thước. Sau khi người dùng đồng ý, installer được tải streaming vào `%LOCALAPPDATA%\TEStudio\updates`; TE kiểm tra byte count và SHA-256 **trong manifest đã ký** trước khi mở installer và thoát. Inno Setup chờ/đóng ứng dụng trước khi thay file. Lỗi tải/kiểm tra xóa file `.part`, giữ bản đang cài. SHA-256 tự công bố trên Release không đủ xác thực nếu thiếu chữ ký từ khóa tin cậy.
