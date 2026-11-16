# TE Studio Phase 4A.3 — Studio Connector Side Panel

Studio Connector là tiện ích Chrome Manifest V3 chỉ đọc các mục kênh **đang được YouTube Studio hiển thị** trong menu chuyển kênh. Bấm biểu tượng extension để mở Side Panel, sau đó mở menu chuyển kênh trong tab Studio và bấm **Quét danh sách (20 giây)**. Side Panel vẫn mở khi bạn cuộn menu. Content script quan sát các mục hiện ra theo thời gian, kể cả phần tử được thêm sau khi cuộn và shadow root đang mở. Danh sách thu được không mặc nhiên là toàn bộ quyền truy cập: Studio có thể tải từng phần hoặc thay DOM. `complete_channel_list` luôn là `false`.

Bảng Side Panel hiển thị tên có thể sửa, handle đọc được, Channel ID và nguồn chứng cứ. Bản ghi có ID từ URL Studio hoặc liên kết Studio được phân biệt với ứng viên chưa có ID. Tên sửa tay được đánh dấu `user_edited`, giữ qua các lần quét; trùng tên hoặc handle được đánh dấu để người dùng kiểm tra. Mục chưa có ID không được tự ghép với một ID chỉ vì trùng tên hoặc handle. Có thể chọn **Xác minh ID (tùy chọn)** cho ứng viên, mở đúng kênh đó trong Studio, đối chiếu URL `UC…`, rồi xác nhận thủ công trong Side Panel. Việc này được đánh dấu `studio_url_user_confirmed`, không được trình bày như tên do DOM xác minh.

**Lưu và đồng bộ TE** giữ catalog trong `chrome.storage.local` trước khi gửi qua Native Messaging `com.testudio.studio_connector`. Nếu host lỗi hoặc quá hạn, catalog vẫn ở Chrome và có thể bấm lưu lại. TE lưu catalog tại `%LOCALAPPDATA%\TEStudio\studio-connector\scan.json`, gộp theo Channel ID và giữ các bản ghi cũ khi lần quét mới thấy ít mục hơn. Vào **7. Xuất bản YouTube** → **Quét kênh Studio** để nạp catalog vào giao diện TE. Bản ghi đủ tên và ID có thể chọn làm đích dự kiến; **upload vẫn bị khóa**. Phát hiện danh tính không xác minh quyền quản lý kênh hoặc quyền upload. OAuth và YouTube API hoạt động độc lập.

Connector chỉ đọc vùng nhận diện kênh đang mở và các mục trong menu chuyển kênh. Nó không đọc cookie, token, mật khẩu, nội dung video, gọi API nội bộ hay tự chuyển kênh. Native Host giới hạn kích thước thông điệp, kiểm tra schema/URL/ID và chỉ cho phép Extension ID đã đăng ký. Bản ghi quá 24 giờ hiện `unavailable` cho đến khi quét lại.

**Chưa kiểm tra DOM thật của YouTube Studio trên Windows.** Các bài kiểm thử DOM giả lập chỉ xác nhận thuật toán và luồng lưu/đồng bộ, không chứng minh Chrome thật hiển thị các thuộc tính mà selector cần. Không phát triển upload tự động trước khi người dùng kiểm tra việc quét nhiều kênh thật.

## Cập nhật trên Windows

```powershell
cd D:\TE-Git
git fetch origin
git switch te-v1-render-test
git pull --ff-only origin te-v1-render-test
.\.venv\Scripts\python.exe -m pip install -e '.[gui]'
```

Mở `chrome://extensions`, bấm **Reload** cho TE Studio Connector và tải lại tab `studio.youtube.com` để content script mới chạy. Bấm biểu tượng extension để mở Side Panel. **Không cần chạy lại Native Host installer nếu đã cài host từ bản gần đây và Extension ID vẫn giữ nguyên.** Nếu chưa cài host, Extension ID thay đổi, hoặc Chrome báo không tìm thấy host, chạy:

```powershell
cd D:\TE-Git
.\scripts\install-studio-connector.ps1 -ExtensionId '<ID-trên-chrome-extensions>'
```

Installer có bản sửa C# đổi biến `header` bị trùng thành `frameHeader` và `requestHeader`. Nếu máy đang dùng launcher cũ chưa chứa bản sửa đó, đóng Chrome, chạy lại installer, rồi mở Chrome. Không cần gỡ extension hay xóa catalog. Installer ghi host vào `%LOCALAPPDATA%\TEStudio\studio-connector` và đăng ký trong HKCU.

## Kiểm tra trên Chrome Windows

1. Mở một tab Studio tại `/channel/UC…`, bấm biểu tượng extension để mở Side Panel.
2. Bấm **Quét danh sách (20 giây)**, mở menu chuyển kênh rồi cuộn từ đầu tới cuối trong thời gian quét. Kiểm tra số bản ghi, tên, handle, ID và nguồn từng trường. Nếu menu đang mở trước khi mở Side Panel và tự đóng, mở lại menu sau khi panel đã xuất hiện.
3. Sửa thử một tên, bấm **Lưu và đồng bộ TE**, đóng/mở Side Panel và quét lại: tên sửa phải còn. Đổi sang tab kênh khác và quét tiếp; bản ghi cũ phải còn.
4. Mở TE Studio, vào **7. Xuất bản YouTube** → **Quét kênh Studio**; kiểm tra catalog. Khởi động lại Chrome và TE để kiểm tra dữ liệu còn nguyên. Không thử upload qua Connector.
5. Nếu thiếu mục, ghi nhận số kênh thật trong menu, số mục bảng thu được và liệu cuộn menu có thêm mục hay không. Không gửi cookie, token hoặc dữ liệu đăng nhập.

Nếu báo content script không có, Reload extension rồi tải lại tab Studio. Nếu Native Host thiếu hoặc Extension ID không khớp, chạy lại installer với ID hiện tại. Nếu host lỗi, catalog vẫn được giữ trong Chrome và có thể đồng bộ lại. Log Native Host ở `%LOCALAPPDATA%\TEStudio\studio-connector\connector.log` chỉ ghi thời gian/chặng/lỗi, không ghi credential.

Kiểm thử không cần tài khoản Studio:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_studio_connector.py tests\test_studio_project_open.py -q
```
