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

function diagnosticText(diagnostics) {
  const value = diagnostics || {};
  return `ID kênh đang mở: ${value.active_id_found ? "có" : "không"}; ` +
    `tên sidebar/header: ${value.active_name_found ? "có" : "không"}; ` +
    `menu chuyển kênh: ${value.switcher_detected ? "có" : "không"}; ` +
    `mục menu: ${value.switcher_entries ?? 0}; tên đọc được: ${value.switcher_names ?? 0}; ` +
    `danh tính xác nhận: ${value.verified_identities ?? 0}.`;
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

async function runScan(chromeApi, setStatus, limits = {}, manualName = "") {
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
  if (manualName) {
    const name = manualName.trim();
    if (!name || name.length > 160 || /[\x00-\x1f]/.test(name)) throw new Error("Tên kênh nhập tay không hợp lệ.");
    const active = result.channels?.find(item => item.id && item.source_url === result.page_url);
    if (!active) throw new Error("URL Studio chưa xác nhận Channel ID của kênh đang mở.");
    if (!active.name) result.manual_name = name;
  }
  setStatus("Đang gửi dữ liệu đến Native Host…");
  const reply = await sendToHost(chromeApi, result, setStatus, limits.host || 15000);
  const details = diagnosticText(reply.diagnostics);
  if (!Number.isInteger(reply.verified_count) || !Number.isInteger(reply.id_count))
    throw new Error("Native Host không trả về số lượng kênh hợp lệ. " + details);
  if (reply.verified_count === 0 && reply.id_count === 0)
    throw new Error("Không tìm thấy Channel ID hợp lệ trong lần quét này. " + details);
  if (reply.user_named_count > 0) {
    setStatus(`Đã lưu ID từ URL cùng tên nhập tay. Tên chưa được DOM xác minh; quyền upload chưa xác minh. ${details}\nMở TE → 7. Xuất bản YouTube → Quét kênh Studio để xem.`);
    return reply;
  }
  if (reply.verified_count === 0) {
    setStatus(`Đã lưu ID kênh nhưng thiếu tên hiển thị; danh tính chưa hoàn chỉnh. ${details}\nMở TE → 7. Xuất bản YouTube → Quét kênh Studio để xem.`);
    return reply;
  }
  setStatus(`Hoàn tất: ${reply.verified_count} danh tính xác nhận trong lần quét, ${reply.count} bản ghi đã lưu. ${details}\nMở TE → 7. Xuất bản YouTube → Quét kênh Studio để xem.`);
  return reply;
}

async function runDiagnostic(chromeApi) {
  const [tab] = await timed(chromeApi.tabs.query({active: true, currentWindow: true}), 3000,
    "Chrome không trả về tab đang mở.");
  if (!tab?.url?.startsWith(STUDIO)) throw new Error("Mở tab studio.youtube.com trước.");
  const result = await timed(chromeApi.tabs.sendMessage(tab.id, {type: "diagnose_channel_identity"}),
    8000, "Content script không phản hồi; tải lại tab Studio và extension.");
  if (!result || result.error || !Array.isArray(result.candidates))
    throw new Error(result?.error || "Không đọc được các phần tử nhận diện kênh.");
  return result;
}

if (typeof document !== "undefined") {
  const status = document.getElementById("status");
  const button = document.getElementById("scan");
  const manual = document.getElementById("manual");
  const manualButton = document.getElementById("save-manual");
  const manualName = document.getElementById("manual-name");
  const diagnosticButton = document.getElementById("diagnose");
  const candidates = document.getElementById("candidates");
  async function scan(name = "") {
    button.disabled = true;
    manualButton.disabled = true;
    try {
      const reply = await runScan(chrome, value => { status.textContent = value; }, {}, name);
      manual.hidden = reply.active_status !== "identity_incomplete";
    } catch (error) {
      status.textContent = `Thất bại: ${error.message || error}`;
      console.error("TE Studio Connector scan failed");
    } finally { button.disabled = false; manualButton.disabled = false; }
  }
  button.addEventListener("click", async () => {
    await scan();
  });
  manualButton.addEventListener("click", async () => {
    if (!manualName.value.trim()) {
      status.textContent = "Thất bại: Nhập tên kênh đang mở trước khi lưu tên tay.";
      return;
    }
    await scan(manualName.value);
  });
  diagnosticButton.addEventListener("click", async () => {
    diagnosticButton.disabled = true;
    candidates.textContent = "Đang đọc vùng nhận diện kênh…";
    try {
      const result = await runDiagnostic(chrome);
      candidates.textContent = `ID URL: ${result.active_id_found ? "có" : "không"}; ${result.candidates.length} phần tử ứng viên.\n` +
        result.candidates.map(item => JSON.stringify(item)).join("\n");
    } catch (error) { candidates.textContent = `Chẩn đoán thất bại: ${error.message || error}`; }
    finally { diagnosticButton.disabled = false; }
  });
}

if (typeof module !== "undefined") module.exports = {timed, nativeError, diagnosticText, sendToHost,
  runScan, runDiagnostic};
