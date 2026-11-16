"use strict";
const status = document.getElementById("status");
document.getElementById("scan").addEventListener("click", async () => {
  status.textContent = "Đang đọc giao diện Studio…";
  try {
    const [tab] = await chrome.tabs.query({active: true, currentWindow: true});
    if (!tab?.url?.startsWith("https://studio.youtube.com/")) throw new Error("Mở tab studio.youtube.com trước.");
    const result = await chrome.tabs.sendMessage(tab.id, {type: "scan_visible_studio"});
    if (!result || result.error) throw new Error(result?.error || "Không đọc được tab Studio; tải lại tab rồi thử.");
    if (result.page_url !== result.page_url_after) throw new Error("Kênh đã đổi trong lúc quét; thử lại.");
    const reply = await chrome.runtime.sendNativeMessage("com.testudio.studio_connector", result);
    if (!reply?.ok) throw new Error(reply?.error || "Native host không phản hồi; kiểm tra cài đặt.");
    status.textContent = `Đã gửi ${reply.count} bản ghi đến TE. Mở TE → 7. Xuất bản YouTube → Quét kênh Studio để xem.\nDanh sách đầy đủ chưa được xác nhận.`;
  } catch (error) { status.textContent = `Không quét được: ${error.message || error}`; }
});
