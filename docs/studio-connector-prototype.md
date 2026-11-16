# TE Studio Phase 4A.3 — Studio Connector feasibility prototype

## Khả năng hiện có và giới hạn

Extension MV3 trong `studio_connector_extension/` chỉ chạy trên `https://studio.youtube.com/*`. Khi người dùng bấm **Quét kênh Studio** trong popup extension, content script đọc URL kênh đang hoạt động và các mục **đang hiển thị** trong menu chuyển kênh. Nó không bấm chuyển kênh, đọc cookie/token, gọi endpoint nội bộ của YouTube hay upload video. Tên đi kèm channel ID chỉ được đánh dấu `identity_verified` khi ID khớp một URL `studio.youtube.com/channel/UC…` quan sát được; tên không có ID giữ trạng thái `discovered`. Mọi mục đều mang `permission_unverified`. Nếu trang đổi URL trong lúc quét, kết quả bị từ chối. `complete_channel_list` luôn là `false`.

Khi URL kênh đang mở có ID `UC…` nhưng không tìm được tên ở thành phần nhận diện kênh trong sidebar/header, TE vẫn giữ ID với trạng thái `identity_incomplete`; kênh đó chưa chọn được làm đích. Popup hiển thị riêng: có/không ID đang mở, có/không tên ở sidebar/header, có/không menu chuyển kênh, số mục menu, số tên đọc được và số danh tính xác nhận. Các bộ chọn tên chỉ đi qua thành phần nhận diện kênh của sidebar/header (kể cả shadow root), không lấy văn bản tùy ý từ trang.

Nếu tên vẫn thiếu, bấm **Chẩn đoán tên kênh** trong popup. Chế độ này chỉ hiển thị tối đa 24 phần tử ứng viên ở vùng nhận diện kênh phía trên sidebar/header: tag, ID, class, thuộc tính accessibility và mẫu văn bản ngắn của phần tử tên. Nó không gửi các ứng viên đến Native Host, không ghi log, không đọc nội dung video hay ô nhập; email trong mẫu bị che. Hãy đối chiếu ứng viên với tên nhìn thấy trước khi báo lại selector cần sửa. DOM thật của tài khoản Windows chưa được Codex kiểm tra.

Nếu URL đã có ID nhưng tên DOM vẫn thiếu, nhập tên hiển thị vào ô **Tên kênh đang mở (nhập tay)** và bấm **Lưu tên nhập tay**. Popup quét lại trang ngay lúc lưu; Native Host chỉ gắn tên với ID của URL kênh đang mở. TE lưu `id_source=studio_url`, `name_source=user_supplied`, `status=identity_user_named`; đây **không phải tên được DOM xác minh** và cũng không chứng minh quyền upload. Tên nhập tay có thể dùng để chọn đích dự kiến trong TE, nhưng upload vẫn bị khóa. Nếu về sau DOM xác nhận tên của cùng ID, bản ghi được nâng lên `identity_verified`.

Chrome Native Messaging gửi một thông điệp đến host `com.testudio.studio_connector`. Manifest host chỉ cho phép **ID extension đã cài**; launcher cũng kiểm tra origin argument Chrome truyền vào. Host Python chỉ nhận thông điệp `scan_results` giới hạn 1 MiB, xác thực schema/URL/ID và ghi bản quét tại `%LOCALAPPDATA%\TEStudio\studio-connector\scan.json`. Không có HTTP listener, lệnh tùy ý, quyền đọc file tùy ý hay truyền credential OAuth. Bản quét là quan sát DOM từ trình duyệt, **không phải xác nhận quyền đăng video**. TE hiển thị loại kết nối `Studio Connector` riêng với `YouTube API`. Bản ghi có ID và tên được phép chọn làm đích trong project, nhưng nút upload vẫn bị khóa cho kết nối đó. Bản ghi quá 24 giờ được hiển thị `unavailable` cho đến khi quét lại. Lần quét sau giữ ID đã từng xác nhận cùng thời điểm quan sát cũ để hỗ trợ người dùng chọn lần lượt từng kênh; không ngầm coi danh sách là đầy đủ.

Thiết kế bridge theo [Chrome Native Messaging](https://developer.chrome.com/docs/extensions/develop/concepts/native-messaging) và [Content scripts](https://developer.chrome.com/docs/extensions/develop/concepts/content-scripts). Chrome kiểm tra `allowed_origins`; một tiến trình khác chạy cùng tài khoản Windows vẫn có thể sửa dữ liệu máy cục bộ, nên bản quét không phải bằng chứng mật mã về danh tính hay quyền kênh.

### Chẩn đoán popup đứng ở bước đọc Studio

Popup hiện lần lượt **Đang đọc giao diện Studio** → **Đang gửi dữ liệu đến Native Host** → **Đang lưu kết quả quét** → **Hoàn tất** hoặc **Thất bại**. Sau 3 giây lấy tab, 8 giây chờ content script, hoặc 15 giây chờ Native Host, popup phải báo lỗi cụ thể thay vì chờ vô hạn. Native Host dùng kết nối có thể ngắt, chuyển đúng một frame Native Messaging và đóng stdin Python sau khi flush; launcher giới hạn thời gian nhận frame và chờ Python. Nguyên nhân treo trên máy Windows chưa được tái hiện trực tiếp, nên thời điểm popup đổi trạng thái sẽ chỉ ra chặng gặp lỗi thực tế.

Nếu báo **Content script chưa có**, trong `chrome://extensions` bấm **Reload** cho extension và tải lại tab `studio.youtube.com`. Nếu báo **Native Host không tìm thấy**, **Extension ID không được cho phép** hoặc **không khởi chạy được**, đóng Chrome, lấy ID extension hiện tại và chạy lại installer bên dưới. Log chỉ ghi thời gian, chặng và mã lỗi, không ghi DOM, cookie, OAuth token hay mật khẩu: `%LOCALAPPDATA%\TEStudio\studio-connector\connector.log`. Chrome cũng có thông báo lỗi Native Messaging trong trang **Errors** của extension tại `chrome://extensions`.

**Chưa có test thực tế trên Windows/YouTube Studio.** DOM của Studio có thể thay đổi. Menu có thể chỉ lộ tên mà không lộ URL/ID, và có thể không liệt kê mọi kênh được ủy quyền. Trong trường hợp đó, người dùng cần chuyển từng kênh thủ công, mở trang Studio của kênh đó và quét lại. Nếu ID vẫn không xuất hiện trong URL hoặc DOM, extension sẽ không xác nhận danh tính. Editor/Manager/Owner và khả năng upload đều chưa được xác minh bởi prototype này.

Để tích lũy nhiều kênh: mở kênh A trong Studio → quét → chuyển thủ công sang kênh B → quét tiếp → vào TE bấm **Quét kênh Studio** để tải danh sách. TE ghép theo ID từ URL, giữ A khi quét B và không dùng tên cũ để gán cho ID khác. Bản quét mới thiếu tên của cùng ID cũng không xóa tên đã từng quan sát được; thời điểm quan sát cũ vẫn được giữ.

Popup extension có thể làm menu chuyển kênh mất focus và đóng khi được mở. Hành vi này **chưa được xác minh trực tiếp trên Chrome Windows**; quy trình hoạt động dựa trên URL và vùng nhận diện của kênh đang mở, không phụ thuộc menu còn mở. Để đăng ký từng kênh được ủy quyền, hãy chuyển kênh trong Studio trước rồi bấm quét.

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

**Sau khi cập nhật từ commit `0981fb5`: đóng Chrome, chạy lại installer với Extension ID hiện tại, mở lại Chrome, bấm Reload cho extension tại `chrome://extensions`, rồi tải lại tab YouTube Studio.** Cần cài lại vì launcher `.exe` đã thay đổi; chỉ `git pull` không cập nhật bản launcher đã biên dịch trong `%LOCALAPPDATA%`.

Sau bản sửa nhận diện tên kênh này, cần **Reload extension** và tải lại tab Studio để content script mới chạy. Python host được cập nhật qua `pip install -e '.[gui]'` và `git pull`.

**Bản sửa lỗi biên dịch C# `header` trong installer:** Sau lần cập nhật chứa bản sửa này, hãy đóng Chrome và chạy lại `install-studio-connector.ps1` với Extension ID hiện tại để thay launcher `.exe` cũ. Sau đó mở Chrome, bấm **Reload** cho extension và tải lại tab Studio. Không cần gỡ extension hoặc xóa các kênh đã quét.

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
