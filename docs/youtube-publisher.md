# TE Studio Phase 4A-API — YouTube Publisher

## Trạng thái và ranh giới an toàn

TE Studio có **owner-only local prototype** dùng OAuth 2.0 Desktop app trong trình duyệt hệ thống và YouTube Data API v3. Chủ kênh cấp quyền riêng cho từng kênh, TE gọi `channels.list(part=snippet,mine=true)` và chỉ đăng ký khi API trả về **đúng một** ID/tên kênh. Việc thấy kênh trong YouTube Studio hoặc danh sách Chrome không được coi là API authorization. Kênh chỉ có quyền Editor qua Studio có thể không xuất hiện qua `mine=true`: [YouTube nói người được mời qua Channel Permissions không dùng được YouTube APIs](https://support.google.com/youtubecreatorstudio/answer/9481328). Mỗi kênh cần một lần OAuth thành công và một `credential_ref` riêng; không giả định một Google login cấp quyền cho tất cả kênh.

OAuth client JSON được sao chép vào cấu hình máy `%LOCALAPPDATA%\TEStudio\youtube-publisher\oauth-desktop-client.json`. Refresh/access token chỉ nằm trong Windows Credential Manager qua `keyring`, khóa bằng `credential_ref`; `studio.json`, `jobs.json`, log và Git không chứa token. Chỉ có scope `youtube.upload` và `youtube.readonly`; không yêu cầu Gmail, Drive, Analytics/revenue hay YouTube Partner. **Không sao chép cấu hình/token này sang máy nhân viên.** Desktop app là public client; bản owner-only này không giải quyết bảo mật cho nhân viên.

Upload tự động chỉ chạy khi **chủ kênh bấm `Tải thử Riêng tư (Owner)` và xác nhận**, trên máy có credential đã đăng ký. TE kiểm tra credential và channel ID lần nữa, tạo upload resumable bằng `videos.insert` với `privacyStatus=private`, tiêu đề/mô tả/tags tiếng Nhật và khai báo trẻ em do người dùng chọn. Nếu có thumbnail JPEG/PNG, TE gọi `thumbnails.set`. Video ID được ghi ngay khi API xác nhận; lỗi sau đó giữ job ở `needs_attention`, không upload lại mù. TE đọc lại `videos.list` để xác nhận đúng kênh và `private`. Chế độ công khai và hẹn giờ chưa hoạt động; `Asia/Tokyo` và kế hoạch `publishAt` chỉ được chuẩn bị, không áp dụng. Monetization và end screen được lưu như tùy chọn, **chưa được cấu hình** qua API.

## Thiết lập Google Cloud trên máy chủ kênh

1. Vào [Google Cloud Console](https://console.cloud.google.com/), tạo hoặc chọn một project cho TE Publisher. Bật **YouTube Data API v3** trong API Library.
2. Trong **Google Auth Platform**, cấu hình brand/consent screen, chọn audience phù hợp. Nếu ở trạng thái **Testing**, thêm tài khoản chủ kênh làm test user. Xem [giới hạn test user](https://support.google.com/cloud/answer/15549945) và [thời hạn refresh token](https://developers.google.com/identity/protocols/oauth2) trước khi vận hành lâu dài.
3. Tạo OAuth client loại **Desktop app** và tải JSON. Không đặt JSON trong `D:\TE-Git` hay project TE. Lưu vào một thư mục riêng của chủ máy. Không gửi JSON hoặc token cho nhân viên.
4. Cập nhật TE Studio bằng PowerShell:

```powershell
cd D:\TE-Git
git fetch origin
git switch te-v1-render-test
git pull --ff-only origin te-v1-render-test
.\.venv\Scripts\python.exe -m pip install -e '.[gui,publisher-api]'
.\.venv\Scripts\python.exe -m te.studio
```

5. Mở **7. Xuất bản YouTube** → **Chọn OAuth JSON (Owner)** → chọn JSON Desktop app. Bấm **Ủy quyền thêm kênh**. Google mở trong trình duyệt hệ thống; chủ kênh tự đăng nhập, chọn tài khoản/kênh nếu Google hiển thị lựa chọn, và đồng ý hai scope. TE không xin mật khẩu. OAuth trả về localhost loopback; thư viện `google-auth-oauthlib` xử lý `state`/PKCE và đổi code lấy token.
6. TE đăng ký kênh chỉ khi `channels.list(mine=true)` xác nhận đúng một ID/tên. So sánh ID/tên với YouTube Studio. Để thêm kênh thứ hai, lặp lại **Ủy quyền thêm kênh** và chọn kênh khác trong Google. Nếu API trả về cùng kênh, kênh không rõ, hoặc quyền Editor-only không có API access, TE chặn đăng ký; không tự gán ID khác.
7. Chọn kênh đã có **OAuth OK**, chọn MP4 Full HD nhỏ của kênh thử, thumbnail nếu cần, nhập tiêu đề/mô tả/tags và khai báo audience. Chọn `immediate` trong metadata (Phase 4A-API vẫn chỉ upload **private**). Bấm **Tải thử Riêng tư (Owner)**, đọc hộp xác nhận đúng kênh, rồi xác nhận. Kiểm tra video ID/URL và trạng thái Private trong YouTube Studio. Không dùng video sản xuất cho lần thử đầu.
8. **Kiểm tra kết nối** gọi API để xác nhận lại kênh; **Thu hồi quyền kênh** gọi endpoint revoke của Google rồi xóa credential cục bộ. Nếu không thể liên hệ Google, TE giữ credential và báo lỗi. **Xóa kết nối cục bộ** chỉ xóa credential trên máy, không tuyên bố Google đã thu hồi grant.

Google OAuth xác minh danh tính và API scopes; channel ownership/authorization là kết quả `channels.list(mine=true)`; khả năng publish còn phụ thuộc quota, giới hạn upload của kênh và chính sách Google. Xem [YouTube API quota và compliance](https://developers.google.com/youtube/v3/guides/quota_and_compliance_audits), [videos.insert](https://developers.google.com/youtube/data/reference/rest/v3/videos/insert), và [thumbnails.set](https://developers.google.com/youtube/data/reference/rest/v3/thumbnails/set). Xem quota thực tế và điều kiện public-upload của project trong Console/tài liệu hiện hành; TE không giả định hạn mức cố định. Tài khoản có thể thu hồi OAuth bất kỳ lúc nào; app Testing có thể cấp refresh token ngắn hạn.

## Ranh giới cho nhân viên và dịch vụ trung tâm tương lai

Owner-only prototype không được triển khai trên máy nhân viên: người có quyền truy cập tài khoản hệ điều hành của chủ máy có thể gọi keyring. Bản production cần dịch vụ trung tâm tin cậy giữ OAuth token và thực thi quyền **ở server**, gồm:

- Owner/admin xác thực riêng, đăng ký/thu hồi channel connection, gán quyền nhân viên theo channel ID, xem log.
- Employee xác thực với dịch vụ, nhận danh sách channel ID/tên được ACL cho phép, gửi video/metadata/job ID qua kết nối bảo mật; không nhận refresh/access token.
- Dịch vụ kiểm tra employee ACL, channel ID, source fingerprint và idempotency key trước mọi upload; lưu job/video ID và audit log, xử lý retry/reconciliation an toàn.

Có thể thiết kế API `GET /v1/channels`, `POST /v1/publish-jobs`, `GET /v1/publish-jobs/{id}`, `POST /v1/admin/channels/authorize`, `DELETE /v1/admin/channels/{id}` với xác thực và ACL server-side. **Chưa có dịch vụ, employee auth hay ACL production trong Phase 4A-API.** Không chuyển token keyring giữa các máy. Các job/channel browser-era và probe UIA cũ vẫn đọc được nhưng không được tự nâng thành OAuth-authorized.

Kiểm thử tự động dùng mock, không đăng nhập Google hay upload thật. Cloud không xác minh luồng OAuth, lựa chọn nhiều kênh hoặc upload trên Windows; các bước thử thực tế phải chạy trên máy chủ kênh.
