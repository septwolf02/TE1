# Upgrade B1: AI phân vai A/B/C/D

## Cài đặt trên Windows

```powershell
cd D:\TE-Git
git switch te-v1-render-test
git pull --ff-only origin te-v1-render-test
py -3.12 -m pip install -e ".[gui,test]"
py -3.12 -m te.studio
```

## Kết nối và sử dụng

Trong **AI Settings**, chọn **ChatGPT** và bấm **Continue with ChatGPT**. Hoàn tất đăng nhập và cấp quyền trong system browser. Nếu thiếu scope `chatgpt.tokens.use.direct`, ứng dụng hiển thị **Permission required**; bấm **Reconnect** để xin lại quyền. Model list lấy từ account đã kết nối.

Hoặc chọn **OpenAI API**, dán key, bấm **Save securely** rồi **Test connection**. Test chỉ lấy model list; nút **AI phân vai** mới gửi inference, có thể phát sinh phí API riêng. Hai bộ credentials độc lập trong Windows Credential Manager. **Disconnect** xóa credentials của provider đang chọn; TE Studio không tự chuyển provider khi gặp lỗi.

Chọn một model trong danh sách của provider. **Nhanh** và **Vừa** tương ứng `low` và `medium` nếu catalog công bố mức reasoning đó; nếu không, ứng dụng bỏ tham số reasoning. Model preference được lưu riêng theo provider.

Sau Full Script Import, mở **AI phân vai**. Điền tên nhân vật A (MC), B (nhân vật chính), C và D. Chọn provider/model rồi bấm **AI phân vai** trong cửa sổ review. Một request phân tích toàn bộ P1–P4 đang có nội dung, bất kể bộ lọc kết quả đang ở **Toàn bài** hay P1–P4. Python tạo ID cho từng đoạn Việt/Nhật; AI chọn ID và Python xác định vị trí trong script. Kết quả hiển thị đoạn Việt, đoạn Nhật, vai và cảnh báo. **Chỉ cần xem lại** giúp tìm đoạn chưa rõ. Chọn dòng, chọn lại text Việt/Nhật trong hai ô và bấm **Thêm / sửa thủ công** nếu cần. Có thể thêm assignment thủ công khi offline. **Xác nhận và lưu** yêu cầu mọi assignment có vai và span Nhật.

Nếu script đổi, assignment cũ được giữ và đánh dấu `stale`. Phân tích lại, hoặc dùng **Tạo mới thủ công** để xóa assignment cũ sau khi xác nhận. **Tải giọng VOICEVOX** đọc speaker/style từ engine; **Lưu giọng cho vai** lưu style ID theo vai. B1 chỉ lưu dữ liệu chuẩn bị cho B2; render hiện tại vẫn dùng một giọng.

## So sánh hai provider với cùng script

Dùng cùng một project, cùng tên nhân vật và ví dụ nhỏ này:

```text
Việt:
P3
MC mở lời. C nói: “Đây là bước ngoặt.” MC kết lại.
P4
MC giới thiệu. D nhận xét: “Cần chờ thêm.”

Nhật:
P3
司会が始める。Cは「転機です」と語る。司会が締める。
P4
司会が紹介する。Dは「もう少し待つべき」と述べる。
```

Chọn ChatGPT, tải models, chọn model, bấm **AI phân vai**; ghi vai, căn chỉnh, thời gian, lỗi/reconnect và token nếu completion trả usage. Sau đó chọn API và lặp lại với cùng script. Ước tính phí API từ token đo được và giá hiện hành của model. Không suy đoán token hay phí nếu provider không trả usage. Mỗi lượt inference chỉ chạy khi người dùng bấm nút.

ChatGPT plan phụ thuộc quyền account/workspace và khả năng phục vụ của OpenAI. API key phụ thuộc billing/quota riêng. B2 mới chia TTS theo vai và render đa giọng.
