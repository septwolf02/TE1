# TE Studio Phase 4A.3 — Studio Connector Side Panel

## Phase 4B.2 — Chuẩn bị thử tải Riêng tư

Nút **Chuẩn bị tải thử Riêng tư qua Studio (không upload)** chỉ hoạt động khi kênh Studio đã được đối chiếu với URL `UC…` trong 24 giờ gần đây, có MP4, thumbnail JPG/PNG và tiêu đề. Sau khi người dùng xác nhận tên kênh, tên file và `PRIVATE`, TE kiểm tra header và kích thước file, tính hash MP4 trong luồng nền, rồi lưu một tác vụ `prepared` cục bộ. Cùng dự án, Channel ID và hash MP4 dùng lại tác vụ; nếu tác vụ có kết quả chưa rõ, TE từ chối tạo lại. Kế hoạch gồm mô tả, tags, audience và `visibility=private`. Không gửi file hoặc metadata qua Native Messaging, không mở form upload, không upload video và không tạo Video ID.

Giới hạn kỹ thuật: Chrome content script không đọc được tệp theo đường dẫn Windows của TE. Native Host hiện chỉ chuyển thông điệp nhỏ; chưa có luồng chuyển MP4 lớn được kiểm chứng. Các phần tử của form upload YouTube Studio cũng chưa được xác minh trên Chrome Windows thật. Vì vậy chưa thể bảo đảm một MP4 được tải đúng kênh và giữ Private; không nên gọi tác vụ `prepared` là upload thành công. Bước thử trên Windows hiện chỉ là chuẩn bị: xác minh kênh theo mục 4B.1, chọn MP4/thumbnail thử, nhập tiêu đề, mô tả, tags và audience, bấm nút chuẩn bị, rồi xác nhận tác vụ hiển thị `prepared` và Studio không có video mới. Upload thử đầu tiên phải đợi cơ chế chọn tệp và giao diện Studio được kiểm chứng riêng.

## Phase 4B.1 — Xác minh kênh đã chọn

Trong **7. Xuất bản YouTube**, chọn đúng một hàng Studio Connector rồi bấm **Mở và xác minh kênh trong Studio**. TE ghi yêu cầu cục bộ có `request_id` ngẫu nhiên và thời hạn hai phút; Native Messaging hiện chỉ nhận kết nối từ extension nên TE không thể tự đẩy lệnh sang Chrome. Trong Side Panel bấm **Nhận yêu cầu từ TE**. Nếu ID đã có chứng cứ Studio, extension mở tab theo ID đó; nếu ID nhập tay hoặc còn thiếu, hãy tự mở đúng kênh trong YouTube Studio. Bấm **Đối chiếu và xác nhận URL đang mở**, xem tên kênh TE, tên đọc được trong Studio và ID URL, rồi xác nhận rõ ràng. Nếu ID nhập tay không khớp URL, sửa ID trong TE và tạo yêu cầu mới. Không ghép theo tên, kể cả tên trùng.

TE chỉ nhận kết quả khi `request_id` còn hiệu lực, bản ghi và ID chưa thay đổi, URL `studio.youtube.com/channel/UC…` ổn định, ID không xung đột với bản ghi khác. Chứng cứ URL, thời điểm và tên Studio đọc được lưu cục bộ; tên hiển thị người dùng đã sửa được giữ nguyên. TE tự kiểm tra kết quả khoảng hai giây một lần trong khi yêu cầu còn hiệu lực. Xác minh danh tính **không** xác minh quyền upload, không mở API OAuth cho kênh Studio và không upload video.

Studio Connector là tiện ích Chrome Manifest V3 chỉ đọc các mục kênh **đang được YouTube Studio hiển thị** trong vùng menu do người dùng xác nhận. Bấm biểu tượng extension để mở Side Panel, mở menu chuyển kênh, bấm **Chọn vùng danh sách kênh**, rồi nhấp một hàng kênh. Cú nhấp bị chặn trước khi chuyển kênh. Extension đề xuất vùng có các hàng cùng cấu trúc và tô sáng vùng đó; chỉ bấm **Xác nhận vùng tô sáng** nếu đó đúng là danh sách kênh. Có thể chọn vùng đề xuất khác hoặc **Hủy chọn vùng**; phím Esc và hết hạn 30 giây cũng thoát chế độ chọn. Sau đó bấm **Quét danh sách (20 giây)** và cuộn menu. Content script chỉ quan sát vùng đã xác nhận. `ytcp-navigation-drawer`, `ul#main-menu` và các mục điều hướng trái bị loại khỏi chẩn đoán và quét. Danh sách thu được không mặc nhiên là toàn bộ quyền truy cập; `complete_channel_list` luôn là `false`.

Bảng Side Panel hiển thị tên có thể sửa, handle đọc được, Channel ID và nguồn chứng cứ. Bản ghi có ID từ URL Studio hoặc liên kết Studio được phân biệt với ứng viên chưa có ID. Tên sửa tay được đánh dấu `user_edited`, giữ qua các lần quét; trùng tên hoặc handle được đánh dấu để người dùng kiểm tra. Mục chưa có ID không được tự ghép với một ID chỉ vì trùng tên hoặc handle. Có thể chọn **Xác minh ID (tùy chọn)** cho ứng viên, mở đúng kênh đó trong Studio, đối chiếu URL `UC…`, rồi xác nhận thủ công trong Side Panel. Việc này được đánh dấu `studio_url_user_confirmed`, không được trình bày như tên do DOM xác minh.

**Lưu và đồng bộ TE** giữ catalog trong `chrome.storage.local` trước khi gửi qua Native Messaging `com.testudio.studio_connector`. Nếu host lỗi hoặc quá hạn, catalog vẫn ở Chrome và có thể bấm lưu lại. TE lưu catalog tại `%LOCALAPPDATA%\TEStudio\studio-connector\scan.json`, gộp theo Channel ID và giữ các bản ghi cũ khi lần quét mới thấy ít mục hơn. Vào **7. Xuất bản YouTube** → **Quét kênh Studio** để nạp catalog vào bảng kênh có thanh cuộn ngang/dọc. Chọn một hàng Studio, nhập ID dạng `UC` và 22 ký tự, rồi bấm **Gắn / sửa ID**. ID này có nguồn `user_supplied`, không được coi là đã xác minh. Mở lại Side Panel để lấy thay đổi từ TE; bấm **Lưu và đồng bộ TE** để ghi catalog về Chrome và TE. ID trùng với bản ghi khác bị từ chối. Có thể sửa ID nhập tay; ID có chứng cứ Studio không sửa bằng nhập tay.

Chọn kênh Studio trong bảng để lưu tiêu đề, mô tả, tags, Full HD MP4 và thumbnail bằng **Lưu bản nháp Studio (không upload)**. Bản nháp chỉ là dữ liệu cục bộ trong Publisher job store; chưa mở trình duyệt, upload hay đổi cài đặt YouTube. Kênh thiếu ID cũng có thể chuẩn bị metadata theo khóa catalog ổn định. Nút **Tải thử Riêng tư (Owner)** chỉ hoạt động với kênh YouTube API đã được OAuth cấp quyền. Channel ID hợp lệ không xác minh quyền quản lý hoặc quyền upload.

Connector chỉ đọc vùng nhận diện kênh đang mở và các mục trong menu chuyển kênh. Nó không đọc cookie, token, mật khẩu, nội dung video, gọi API nội bộ hay tự chuyển kênh. Native Host giới hạn kích thước thông điệp, kiểm tra schema/URL/ID và chỉ cho phép Extension ID đã đăng ký. Bản ghi quá 24 giờ hiện `unavailable` cho đến khi quét lại.

**Báo cáo Windows đã xác nhận chẩn đoán trước đây nhầm navigation drawer với menu kênh.** Chưa kiểm thử luồng chọn vùng mới trên Chrome Windows. Các bài kiểm thử DOM giả lập không chứng minh bulk discovery hoạt động trên Studio thật. Không phát triển upload tự động trước khi người dùng xác nhận việc quét nhiều kênh thật.

## Chẩn đoán menu kênh trên Chrome thật

Khi menu chuyển kênh đang mở, bấm **Chẩn đoán menu kênh** trong Side Panel và cuộn menu trong 3 giây. Chẩn đoán tự động loại thanh điều hướng trái. Sau khi xác nhận vùng, Side Panel hiển thị cấu trúc vùng và các hàng kênh đã đọc: tên, handle và ID chỉ khi có URL Studio tương ứng. Báo cáo vùng đã chọn có thể chứa **tên kênh và handle**; hãy kiểm tra nội dung trước khi bấm **Sao chép báo cáo đã lọc** và chia sẻ. Báo cáo không chứa email, URL đầy đủ, token hay cookie, và không gửi cho Native Host hoặc máy chủ nào. Dấu hiệu tải động hoặc virtualization vẫn cần kiểm chứng khi cuộn trên Chrome thật.

Các bộ đếm tách riêng mục menu đã quan sát trong lượt quét hiện tại, catalog tích lũy, ID có chứng cứ Studio, ID nhập tay, ứng viên thiếu ID và số bản ghi ở TE sau lần đồng bộ gần nhất. Catalog tích lũy có thể chứa bản ghi từ lượt quét trước; không coi chúng là mục vừa được phát hiện. Chẩn đoán không thay đổi catalog. Nếu báo cáo không nhận diện được menu nào, thông tin cấu trúc đó là bằng chứng để sửa bộ chọn ở bước tiếp theo; hiện chưa thêm bộ chọn DOM mới theo suy đoán.

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
2. Khi Side Panel đã mở, mở menu chuyển kênh. Bấm **Chọn vùng danh sách kênh**, nhấp một hàng kênh, kiểm tra vùng tô sáng và số hàng đề xuất, rồi **Xác nhận vùng tô sáng**. Nếu vùng sai, Hủy và chọn lại. Nếu menu tự đóng lúc mở Side Panel, mở lại sau khi panel xuất hiện.
3. Bấm **Quét danh sách (20 giây)** và cuộn từ đầu tới cuối. Ghi riêng số mục menu lượt này và số bản ghi catalog tích lũy. Sao chép báo cáo vùng đã chọn sau khi kiểm tra nội dung, gửi lại để đánh giá DOM thật và kết quả quét.
4. Sửa thử một tên, bấm **Lưu và đồng bộ TE**, đóng/mở Side Panel và quét lại: tên sửa phải còn. Đổi sang tab kênh khác và quét tiếp; bản ghi cũ phải còn.
5. Mở TE Studio, vào **7. Xuất bản YouTube** → **Quét kênh Studio**; kiểm tra catalog. Chọn một kênh chưa có ID, nhập ID, bấm **Gắn / sửa ID**. Mở lại Side Panel, bấm **Lấy catalog từ TE**, xác nhận ID có nhãn `user_supplied`, rồi bấm **Lưu và đồng bộ TE**. Khởi động lại Chrome và TE để kiểm tra dữ liệu còn nguyên. Có thể sửa ID nhập tay nếu sai. Không thử upload qua Connector.
6. Nếu thiếu mục, ghi nhận số kênh thật trong menu và số mục quét được. Không gửi cookie, token hoặc dữ liệu đăng nhập.

Nếu báo content script không có, Reload extension rồi tải lại tab Studio. Nếu Native Host thiếu hoặc Extension ID không khớp, chạy lại installer với ID hiện tại. Nếu host lỗi, catalog vẫn được giữ trong Chrome và có thể đồng bộ lại. Log Native Host ở `%LOCALAPPDATA%\TEStudio\studio-connector\connector.log` chỉ ghi thời gian/chặng/lỗi, không ghi credential.

Kiểm thử không cần tài khoản Studio:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_studio_connector.py tests\test_studio_project_open.py -q
```
