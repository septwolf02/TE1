"use strict";

const HOST = "com.testudio.studio_connector";
const STUDIO = "https://studio.youtube.com/";

function timed(promise, milliseconds, message) {
  let timer;
  return Promise.race([promise, new Promise((_, reject) => {
    timer = setTimeout(() => reject(new Error(message)), milliseconds);
  })]).finally(() => clearTimeout(timer));
}

function nativeError(message) {
  const detail = String(message || "").toLowerCase();
  if (detail.includes("not found") || detail.includes("not registered"))
    return "Không tìm thấy Native Host. Chạy lại scripts/install-studio-connector.ps1 với Extension ID hiện tại.";
  if (detail.includes("forbidden") || detail.includes("access") || detail.includes("permission"))
    return "Extension ID không được Native Host cho phép. Kiểm tra ID trong chrome://extensions rồi cài lại host.";
  if (detail.includes("failed to start") || detail.includes("could not start") || detail.includes("launch"))
    return "Không khởi chạy được Native Host. Kiểm tra file .exe và đường dẫn Python trong bản cài.";
  return "Native Host ngắt kết nối hoặc Python dừng lỗi. Kiểm tra diagnostics của Studio Connector và cài lại host nếu cần.";
}

function sendToHost(chromeApi, payload, setStatus, timeoutMs = 15000) {
  return new Promise((resolve, reject) => {
    let port;
    let done = false;
    let timer;
    const finish = (error, result) => {
      if (done) return;
      done = true;
      clearTimeout(timer);
      if (port) port.disconnect();
      if (error) reject(error); else resolve(result);
    };
    try {
      port = chromeApi.runtime.connectNative(HOST);
      port.onMessage.addListener(reply => {
        if (reply?.stage === "saving") {
          setStatus("Đang lưu kết quả quét…");
        } else if (reply?.ok === true) {
          finish(null, reply);
        } else {
          finish(new Error(reply?.error || "Native Host trả về thông điệp không hợp lệ."));
        }
      });
      port.onDisconnect.addListener(() => {
        if (!done) finish(new Error(nativeError(chromeApi.runtime.lastError?.message)));
      });
      timer = setTimeout(() => finish(new Error("Native Host không phản hồi trong 15 giây. Kiểm tra launcher/Python và thử lại.")), timeoutMs);
      port.postMessage(payload);
    } catch (error) {
      finish(new Error(nativeError(error?.message)));
    }
  });
}

async function runScan(chromeApi, setStatus, limits = {}) {
  setStatus("Đang đọc giao diện Studio…");
  const [tab] = await timed(chromeApi.tabs.query({active: true, currentWindow: true}),
    limits.tab || 3000, "Chrome không trả về tab đang mở trong 3 giây.");
  if (!tab?.url?.startsWith(STUDIO)) throw new Error("Mở tab studio.youtube.com trước.");
  let result;
  try {
    result = await timed(chromeApi.tabs.sendMessage(tab.id, {type: "scan_visible_studio"}),
      limits.dom || 8000, "Content script không phản hồi trong 8 giây. Tải lại tab YouTube Studio và reload extension.");
  } catch (error) {
    if (/receiving end|connection|could not establish/i.test(error?.message || ""))
      throw new Error("Content script chưa có trên tab Studio. Tải lại tab và reload extension ở chrome://extensions.");
    throw error;
  }
  if (!result || result.error) throw new Error(result?.error || "Content script trả về dữ liệu rỗng; tải lại tab Studio.");
  if (result.page_url !== result.page_url_after) throw new Error("Kênh đã đổi trong lúc quét; mở lại menu rồi thử lại.");
  setStatus("Đang gửi dữ liệu đến Native Host…");
  const reply = await sendToHost(chromeApi, result, setStatus, limits.host || 15000);
  if (!Number.isInteger(reply.verified_count) || reply.verified_count === 0)
    throw new Error("Không tìm thấy kênh có tên và ID xác nhận được. Mở trang của từng kênh, mở menu chuyển kênh rồi quét lại; bản ghi chỉ có tên vẫn được lưu.");
  setStatus(`Hoàn tất: ${reply.verified_count} kênh có ID, ${reply.count} bản ghi. Mở TE → 7. Xuất bản YouTube → Quét kênh Studio để xem.`);
  return reply;
}

if (typeof document !== "undefined") {
  const status = document.getElementById("status");
  const button = document.getElementById("scan");
  button.addEventListener("click", async () => {
    button.disabled = true;
    try { await runScan(chrome, value => { status.textContent = value; }); }
    catch (error) {
      status.textContent = `Thất bại: ${error.message || error}`;
      console.error("TE Studio Connector scan failed");
    } finally { button.disabled = false; }
  });
}

if (typeof module !== "undefined") module.exports = {timed, nativeError, sendToHost, runScan};
