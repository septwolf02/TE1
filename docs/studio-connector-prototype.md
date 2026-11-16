# TE Studio Phase 4A.3 — Studio Connector feasibility prototype

## Khả năng hiện có và giới hạn

Extension MV3 trong `studio_connector_extension/` chỉ chạy trên `https://studio.youtube.com/*`. Khi người dùng bấm **Quét kênh Studio** trong popup extension, content script đọc URL kênh đang hoạt động và các mục **đang hiển thị** trong menu chuyển kênh. Nó không bấm chuyển kênh, đọc cookie/token, gọi endpoint nội bộ của YouTube hay upload video. Tên đi kèm channel ID chỉ được đánh dấu `identity_verified` khi ID khớp một URL `studio.youtube.com/channel/UC…` quan sát được; tên không có ID giữ trạng thái `discovered`. Mọi mục đều mang `permission_unverified`. Nếu trang đổi URL trong lúc quét, kết quả bị từ chối. `complete_channel_list` luôn là `false`.

Chrome Native Messaging gửi một thông điệp đến host `com.testudio.studio_connector`. Manifest host chỉ cho phép **ID extension đã cài**; launcher cũng kiểm tra origin argument Chrome truyền vào. Host Python chỉ nhận thông điệp `scan_results` giới hạn 1 MiB, xác thực schema/URL/ID và ghi bản quét tại `%LOCALAPPDATA%\TEStudio\studio-connector\scan.json`. Không có HTTP listener, lệnh tùy ý, quyền đọc file tùy ý hay truyền credential OAuth. Bản quét là quan sát DOM từ trình duyệt, **không phải xác nhận quyền đăng video**. TE hiển thị loại kết nối `Studio Connector` riêng với `YouTube API`. Bản ghi có ID và tên được phép chọn làm đích trong project, nhưng nút upload vẫn bị khóa cho kết nối đó. Bản ghi quá 24 giờ được hiển thị `unavailable` cho đến khi quét lại. Lần quét sau giữ ID đã từng xác nhận cùng thời điểm quan sát cũ để hỗ trợ người dùng chọn lần lượt từng kênh; không ngầm coi danh sách là đầy đủ.

Thiết kế bridge theo [Chrome Native Messaging](https://developer.chrome.com/docs/extensions/develop/concepts/native-messaging) và [Content scripts](https://developer.chrome.com/docs/extensions/develop/concepts/content-scripts). Chrome kiểm tra `allowed_origins`; một tiến trình khác chạy cùng tài khoản Windows vẫn có thể sửa dữ liệu máy cục bộ, nên bản quét không phải bằng chứng mật mã về danh tính hay quyền kênh.

**Chưa có test thực tế trên Windows/YouTube Studio.** DOM của Studio có thể thay đổi. Menu có thể chỉ lộ tên mà không lộ URL/ID, và có thể không liệt kê mọi kênh được ủy quyền. Trong trường hợp đó, người dùng cần chuyển từng kênh thủ công, mở trang Studio của kênh đó và quét lại. Nếu ID vẫn không xuất hiện trong URL hoặc DOM, extension sẽ không xác nhận danh tính. Editor/Manager/Owner và khả năng upload đều chưa được xác minh bởi prototype này.

## Cài đặt trên Windows

1. Cập nhật TE tại `D:\TE-Git`:

```powershell
cd D:\TE-Git
git fetch origin
git switch te-v1-render-test
git pull --ff-only origin te-v1-render-test
.\.venv\Scripts\python.exe -m pip install -e '.[gui]'
```

2. Mở `chrome://extensions`, bật **Developer mode**, chọn **Load unpacked**, trỏ đến `D:\TE-Git\studio_connector_extension`. Sao chép **Extension ID** 32 ký tự hiện trên thẻ extension.
3. Đăng ký Native Messaging host cho đúng Extension ID (không cần sửa Registry bằng tay):

```powershell
cd D:\TE-Git
.\scripts\install-studio-connector.ps1 -ExtensionId '<ID-trên-chrome-extensions>'
```

Nếu PowerShell chặn script theo chính sách máy, chạy phiên PowerShell dành riêng cho bước cài với chính sách do quản trị viên cho phép. Script ghi manifest/exe vào `%LOCALAPPDATA%\TEStudio\studio-connector` và đăng ký tại `HKCU\Software\Google\Chrome\NativeMessagingHosts\com.testudio.studio_connector`; không sửa Chrome profile cá nhân. Native host dùng `.venv\Scripts\python.exe` của bản TE hiện tại.

4. Chạy TE Studio:

```powershell
.\.venv\Scripts\python.exe -m te.studio
```

5. Đăng nhập YouTube Studio bình thường trong Chrome. Mở menu chuyển kênh, bấm icon extension → **Quét kênh Studio**. Sau khi popup báo đã gửi, trong TE vào **7. Xuất bản YouTube** → **Quét kênh Studio** để tải bản quét. Kiểm tra tên, ID, trạng thái và thời điểm quét. Đối chiếu ID với URL thật của từng kênh; với nhiều kênh, chuyển từng kênh thủ công và quét thêm lần nữa. Khởi động lại TE để kiểm tra bản ghi vẫn có mặt.

6. Kiểm thử Python độc lập, không đăng nhập hoặc upload:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_studio_connector.py tests\test_studio_project_open.py -q
```

Gỡ kết nối: vào `chrome://extensions` và **Remove** extension, rồi chạy:

```powershell
cd D:\TE-Git
.\scripts\uninstall-studio-connector.ps1
```

Script gỡ đăng ký host và launcher; giữ `scan.json` để người dùng tự quyết định xóa lịch sử quan sát. Không động đến token YouTube API, project hoặc job Publisher.

## Điều cần báo lại sau test thực tế

- ID extension và thông báo popup có kết nối được Native Messaging host hay không (không gửi token).
- URL Studio của kênh đang hoạt động có ID `UC…` không; tên có hiện đúng không.
- Menu chuyển kênh đang mở có cung cấp URL/ID cho từng mục hay chỉ có tên.
- Có xác nhận được ít nhất hai ID, gồm một kênh được ủy quyền, sau khi tự đối chiếu Studio hay không.
- Danh sách còn sau khi mở lại TE; trạng thái `identity_verified` và `permission_unverified` có đúng không.

Không suy ra khả năng tự động xuất bản từ kết quả này. Nếu danh tính không được xác nhận đáng tin cậy, dừng ở bước feasibility; không mở rộng sang upload, hẹn giờ, kiếm tiền hay end screen.
