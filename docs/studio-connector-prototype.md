# TE Studio Phase 4A.3 — Studio Connector Side Panel

## Phase 4B.2B — MP4 handoff trong Connector hiện có (cần kiểm chứng Windows)

### Sửa lỗi `missing_input` và thử lại sau lỗi trước upload

Connector đợi tối đa 20 giây cho hộp upload xuất hiện. Nếu Studio hiển thị đúng **một** nút có tên truy cập rõ ràng **Upload videos** hoặc **Create** (kể cả tên tiếng Nhật/Việt đã hỗ trợ), Connector bấm nút đó một lần rồi kiểm tra lại. Nó chỉ chọn file input nằm trong hộp upload có chứng cứ cấu trúc; `accept` trống hoặc khác `video/mp4` vẫn được chấp nhận nếu không chỉ dành cho ảnh/âm thanh. Nhiều input phù hợp hoặc hộp upload không xuất hiện sẽ dừng **trước khi chọn tệp**. TE hiện hiển thị bước trình duyệt, trạng thái hộp upload, số input, lý do loại, trạng thái CDP và việc chọn tệp đã bắt đầu hay chưa. Không thu thập nội dung trang hoặc credential vào chẩn đoán.

Nếu job là `failed_before_upload` và request cũ xác nhận chưa bắt đầu chọn tệp, bấm lại nút upload với cùng MP4 sẽ tạo job/attempt mới; lịch sử lỗi cũ được giữ. `needs_attention`, `uploading`, `processing` và mọi kết quả không rõ vẫn bị chặn thử lại. Không cần đổi tên MP4 hoặc xóa JSON. Đây là sửa lỗi theo mock; chưa xác nhận ô input thật trên Windows Studio. **Cho bản sửa này:** Reload extension tại `chrome://extensions`, tải lại tab Studio và cập nhật TE như dưới đây. **Không cần chạy lại Native Host installer** vì launcher C# và manifest không đổi; Python Host sẽ dùng mã mới sau `pip install -e '.[gui]'`.

Nút **Tải thử Riêng tư qua Studio Connector** trong TE dùng tác vụ `prepared` và chỉ bật khi kênh vừa được xác minh bằng URL, MP4/thumbnail và tiêu đề hợp lệ. Sau một hộp xác nhận kênh, tên file và `PRIVATE`, TE tạo `attempt_id`, lưu yêu cầu cục bộ có thời hạn 5 phút và khóa lần thử. Service worker của **extension hiện có** kiểm tra Native Host bằng `chrome.alarms` khoảng 30 giây một lần; không cần mở Side Panel hay nhập đường dẫn. Extension mở trang upload của Channel ID đích, đọc URL Studio qua content script, và chỉ khi ID khớp mới yêu cầu Native Host trả đường dẫn MP4 đã ghi trong tác vụ. Native Host kiểm tra lại hash tệp; web page không được gọi Host hoặc chọn đường dẫn tùy ý. Extension dùng `chrome.debugger` → `DOM.setFileInputFiles` trên **một** input nhận video; nếu thiếu hoặc có nhiều input, nó dừng. Nó đối chiếu tên/kích thước file trong Chrome, rồi ghi `uploading` ở TE. Nó không bấm nút xuất bản, không đánh dấu `completed_private`, không tự gán Video ID.

**Giới hạn quan trọng:** DOM upload và route `/channel/UC…/videos/upload` chưa được kiểm chứng trên YouTube Studio Windows thật. Adapter `chrome.debugger` trong extension hiện có cũng chưa được chạy trên Windows; prototype CDP riêng trước đó chỉ chứng minh lệnh Chromium. Nếu route/input khác, tác vụ sẽ dừng trước khi chọn file. Sau khi Chrome nhận MP4, kết quả và Private vẫn chưa rõ; TE yêu cầu kiểm tra Studio thủ công. Không tự thử lại khi Chrome ngắt kết nối hoặc kết quả mơ hồ. Một khóa cục bộ ngăn lần thử khác cho tới khi trạng thái được đối chiếu; không tự xóa khóa khi đã bắt đầu chọn tệp.

Thử đầu tiên trên Windows: cập nhật và cài lại Native Host như phần **Cập nhật trên Windows** bên dưới; Reload extension tại `chrome://extensions` và chấp nhận quyền `debugger` mới. Mở Chrome và đăng nhập YouTube Studio bằng tài khoản có quyền Editor. Trong TE, chọn kênh Studio có nhãn **Đã xác minh URL Studio** (xác minh lại nếu quá 24 giờ), chọn MP4 thử nhỏ, thumbnail, nhập tiêu đề/mô tả/tags và audience, rồi bấm **Tải thử Riêng tư qua Studio Connector** một lần. Đối chiếu hộp xác nhận. Đợi tối đa khoảng 30 giây để extension nhận yêu cầu, quan sát tab Studio, tên và tiến trình MP4. Nếu Studio yêu cầu hoàn tất biểu mẫu, chỉ chọn **Private** và kiểm tra đúng Channel ID trước khi lưu. TE không tự xác nhận thành công; nếu báo `failed_before_upload` hoặc `needs_attention`, kiểm tra Studio trước khi thử lần khác. Không dùng video sản xuất cho phép thử đầu tiên.

## Phase 4B.2A — Tác vụ chuẩn bị được dùng lại

TE kiểm tra header và kích thước MP4/thumbnail, tính hash MP4 trong luồng nền, rồi lưu một tác vụ `prepared` cục bộ trước khi gửi yêu cầu Connector. Cùng dự án, Channel ID và hash MP4 dùng lại tác vụ; nếu tác vụ có kết quả chưa rõ, TE từ chối tạo lại. Kế hoạch gồm mô tả, tags, audience và `visibility=private`.

Chrome content script không đọc được tệp theo đường dẫn Windows của TE. Bản 4B.2B dùng Native Host để chuyển **đường dẫn MP4 đã được TE cho phép** tới extension và CDP để chọn tệp trong Chrome; không chuyển byte MP4 qua Host. Giao diện upload Studio thật vẫn phải được kiểm chứng trên Windows.

## Phase 4B.1 — Xác minh kênh đã chọn

Trong **7. Xuất bản YouTube**, chọn đúng một hàng Studio Connector rồi bấm **Mở và xác minh kênh trong Studio**. TE ghi yêu cầu cục bộ có `request_id` ngẫu nhiên và thời hạn hai phút; Native Messaging hiện chỉ nhận kết nối từ extension nên TE không thể tự đẩy lệnh sang Chrome. Trong Side Panel bấm **Nhận yêu cầu từ TE**. Nếu ID đã có chứng cứ Studio, extension mở tab theo ID đó; nếu ID nhập tay hoặc còn thiếu, hãy tự mở đúng kênh trong YouTube Studio. Bấm **Đối chiếu và xác nhận URL đang mở**, xem tên kênh TE, tên đọc được trong Studio và ID URL, rồi xác nhận rõ ràng. Nếu ID nhập tay không khớp URL, sửa ID trong TE và tạo yêu cầu mới. Không ghép theo tên, kể cả tên trùng.

TE chỉ nhận kết quả khi `request_id` còn hiệu lực, bản ghi và ID chưa thay đổi, URL `studio.youtube.com/channel/UC…` ổn định, ID không xung đột với bản ghi khác. Chứng cứ URL, thời điểm và tên Studio đọc được lưu cục bộ; tên hiển thị người dùng đã sửa được giữ nguyên. TE tự kiểm tra kết quả khoảng hai giây một lần trong khi yêu cầu còn hiệu lực. Xác minh danh tính **không** xác minh quyền upload, không mở API OAuth cho kênh Studio và không upload video.

Studio Connector là tiện ích Chrome Manifest V3 chỉ đọc các mục kênh **đang được YouTube Studio hiển thị** trong vùng menu do người dùng xác nhận. Bấm biểu tượng extension để mở Side Panel, mở menu chuyển kênh, bấm **Chọn vùng danh sách kênh**, rồi nhấp một hàng kênh. Cú nhấp bị chặn trước khi chuyển kênh. Extension đề xuất vùng có các hàng cùng cấu trúc và tô sáng vùng đó; chỉ bấm **Xác nhận vùng tô sáng** nếu đó đúng là danh sách kênh. Có thể chọn vùng đề xuất khác hoặc **Hủy chọn vùng**; phím Esc và hết hạn 30 giây cũng thoát chế độ chọn. Sau đó bấm **Quét danh sách (20 giây)** và cuộn menu. Content script chỉ quan sát vùng đã xác nhận. `ytcp-navigation-drawer`, `ul#main-menu` và các mục điều hướng trái bị loại khỏi chẩn đoán và quét. Danh sách thu được không mặc nhiên là toàn bộ quyền truy cập; `complete_channel_list` luôn là `false`.

Bảng Side Panel hiển thị tên có thể sửa, handle đọc được, Channel ID và nguồn chứng cứ. Bản ghi có ID từ URL Studio hoặc liên kết Studio được phân biệt với ứng viên chưa có ID. Tên sửa tay được đánh dấu `user_edited`, giữ qua các lần quét; trùng tên hoặc handle được đánh dấu để người dùng kiểm tra. Mục chưa có ID không được tự ghép với một ID chỉ vì trùng tên hoặc handle. Có thể chọn **Xác minh ID (tùy chọn)** cho ứng viên, mở đúng kênh đó trong Studio, đối chiếu URL `UC…`, rồi xác nhận thủ công trong Side Panel. Việc này được đánh dấu `studio_url_user_confirmed`, không được trình bày như tên do DOM xác minh.

**Lưu và đồng bộ TE** giữ catalog trong `chrome.storage.local` trước khi gửi qua Native Messaging `com.testudio.studio_connector`. Nếu host lỗi hoặc quá hạn, catalog vẫn ở Chrome và có thể bấm lưu lại. TE lưu catalog tại `%LOCALAPPDATA%\TEStudio\studio-connector\scan.json`, gộp theo Channel ID và giữ các bản ghi cũ khi lần quét mới thấy ít mục hơn. Vào **7. Xuất bản YouTube** → **Quét kênh Studio** để nạp catalog vào bảng kênh có thanh cuộn ngang/dọc. Chọn một hàng Studio, nhập ID dạng `UC` và 22 ký tự, rồi bấm **Gắn / sửa ID**. ID này có nguồn `user_supplied`, không được coi là đã xác minh. Mở lại Side Panel để lấy thay đổi từ TE; bấm **Lưu và đồng bộ TE** để ghi catalog về Chrome và TE. ID trùng với bản ghi khác bị từ chối. Có thể sửa ID nhập tay; ID có chứng cứ Studio không sửa bằng nhập tay.

Chọn kênh Studio trong bảng để lưu tiêu đề, mô tả, tags, Full HD MP4 và thumbnail bằng **Lưu bản nháp Studio (không upload)**. Bản nháp chỉ là dữ liệu cục bộ trong Publisher job store. Kênh thiếu ID cũng có thể chuẩn bị metadata theo khóa catalog ổn định. Chỉ kênh Studio có xác minh URL gần đây mới dùng được nút **Tải thử Riêng tư qua Studio Connector**; khả năng chọn MP4 trong giao diện upload thật vẫn cần thử trên Windows. Nút **Tải thử Riêng tư (Owner)** chỉ hoạt động với kênh YouTube API đã được OAuth cấp quyền. Channel ID hợp lệ không xác minh quyền quản lý hoặc quyền upload.

Connector chỉ đọc vùng nhận diện kênh đang mở và các mục trong menu chuyển kênh. Nó không đọc cookie, token, mật khẩu, nội dung video, gọi API nội bộ hay tự chuyển kênh. Native Host giới hạn kích thước thông điệp, kiểm tra schema/URL/ID và chỉ cho phép Extension ID đã đăng ký. Bản ghi quá 24 giờ hiện `unavailable` cho đến khi quét lại.

**Báo cáo Windows đã xác nhận chẩn đoán trước đây nhầm navigation drawer với menu kênh.** Các bài kiểm thử DOM giả lập không chứng minh bulk discovery hoặc upload hoạt động trên Studio thật. Bản tích hợp upload hiện chỉ ở mức thử nghiệm an toàn với video nhỏ và kênh đã xác minh.

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

Mở `chrome://extensions`, bấm **Reload** cho TE Studio Connector và tải lại tab `studio.youtube.com` để content script mới chạy. Bấm biểu tượng extension để mở Side Panel. **Chỉ cần chạy Native Host installer nếu máy chưa cài bản 4B.2B hoặc launcher còn cũ**; bản sửa `missing_input` này không thay đổi launcher. Khi cần cài, dùng đúng Extension ID hiện tại:

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
5. Mở TE Studio, vào **7. Xuất bản YouTube** → **Quét kênh Studio**; kiểm tra catalog. Chọn một kênh chưa có ID, nhập ID, bấm **Gắn / sửa ID**. Mở lại Side Panel, bấm **Lấy catalog từ TE**, xác nhận ID có nhãn `user_supplied`, rồi bấm **Lưu và đồng bộ TE**. Khởi động lại Chrome và TE để kiểm tra dữ liệu còn nguyên. Có thể sửa ID nhập tay nếu sai.
6. Nếu thiếu mục, ghi nhận số kênh thật trong menu và số mục quét được. Không gửi cookie, token hoặc dữ liệu đăng nhập.

Nếu báo content script không có, Reload extension rồi tải lại tab Studio. Nếu Native Host thiếu hoặc Extension ID không khớp, chạy lại installer với ID hiện tại. Nếu host lỗi, catalog vẫn được giữ trong Chrome và có thể đồng bộ lại. Log Native Host ở `%LOCALAPPDATA%\TEStudio\studio-connector\connector.log` chỉ ghi thời gian/chặng/lỗi, không ghi credential.

Kiểm thử không cần tài khoản Studio:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_studio_connector.py tests\test_studio_project_open.py -q
```
