"use strict";

function withTimeout(promise, ms, message) {
  let timer;
  return Promise.race([promise, new Promise((_, reject) => {
    timer = setTimeout(() => reject(new Error(message)), ms);
  })]).finally(() => clearTimeout(timer));
}

function nativeSync(chromeApi, records, timeoutMs = 15000) {
  return new Promise((resolve, reject) => {
    let port, done = false, timer;
    const finish = (error, value) => {
      if (done) return;
      done = true; clearTimeout(timer);
      try { port?.disconnect(); } catch (_) { /* connection already closed */ }
      if (error) reject(error); else resolve(value);
    };
    try {
      port = chromeApi.runtime.connectNative("com.testudio.studio_connector");
      port.onMessage.addListener(reply => {
        if (reply?.stage === "saving") return;
        if (reply?.ok === true) finish(null, reply);
        else finish(new Error(reply?.error || "Native Host trả về dữ liệu không hợp lệ."));
      });
      port.onDisconnect.addListener(() => {
        if (!done) finish(new Error("Native Host ngắt kết nối. Bản catalog vẫn được giữ trong Chrome; kiểm tra installer rồi bấm Lưu lại."));
      });
      timer = setTimeout(() => finish(new Error("Native Host quá hạn 15 giây. Catalog vẫn ở Chrome; thử Lưu lại.")), timeoutMs);
      port.postMessage({type:"catalog_sync", schema:1, records});
    } catch (error) { finish(new Error("Không mở được Native Host. Catalog vẫn ở Chrome; kiểm tra cài đặt và Extension ID.")); }
  });
}

async function createPanel(chromeApi, ui, defaults = {}) {
  let rows = [], scanSession = "", tabId = null, dirty = false, revision = 0,
      storageWrite = Promise.resolve(),
      candidateKey = "", lastSnapshot = "";
  const saved = await chromeApi.storage.local.get(["studioCatalog", "studioDraft", "studioUnsynced"]);
  rows = Array.isArray(saved.studioDraft) ? saved.studioDraft : Array.isArray(saved.studioCatalog) ? saved.studioCatalog : [];
  dirty = !!saved.studioUnsynced || JSON.stringify(saved.studioCatalog || []) !== JSON.stringify(rows);
  const status = message => { ui.status.textContent = message; };
  const render = () => {
    ui.rows.replaceChildren();
    ui.counts.textContent = `${rows.length} bản ghi • ${rows.filter(r=>r.id).length} có ID • ` +
      `${rows.filter(r=>!r.id).length} chưa rõ ID • ${dirty ? "chưa đồng bộ TE" : "đã đồng bộ TE"}`;
    for (const row of rows) {
      const tr = document.createElement("tr");
      const nameCell = tr.insertCell();
      const input = document.createElement("input");
      input.value = row.display_name || "";
      input.setAttribute("aria-label", `Tên kênh ${row.id || row.handle || "chưa rõ"}`);
      input.addEventListener("change", () => {
        try { rows = editName(rows, row.key, input.value); revision++; dirty = true; scheduleDraft(); render(); }
        catch (error) { status(error.message); input.value = row.display_name || ""; }
      });
      nameCell.append(input);
      const handleCell = tr.insertCell(); handleCell.textContent = row.handle || "—";
      const idCell = tr.insertCell();
      const id = document.createElement("code"); id.textContent = row.id || "Chưa có ID"; idCell.append(id);
      const source = document.createElement("small");
      source.textContent = `\n${row.id_source || "missing"}; ${row.name_source || "missing"}`; idCell.append(source);
      const stateCell = tr.insertCell();
      stateCell.textContent = `${row.status}; quyền: chưa xác minh; upload: chưa xác minh` +
        (row.duplicate_name || row.duplicate_handle ? " • trùng tên/handle" : "");
      if (!row.id) {
        const verify = document.createElement("button"); verify.textContent = "Xác minh ID (tùy chọn)";
        verify.addEventListener("click", () => {
          candidateKey = row.key;
          status(`Đã chọn “${row.display_name || row.handle}”. Hãy mở đúng kênh này trong Studio, đối chiếu URL, rồi bấm “Xác nhận ID cho ứng viên đã chọn”.`);
        });
        stateCell.append(verify);
      }
      ui.rows.append(tr);
    }
  };
  // Keep each reviewed change before the panel can close or the Native Host can fail.
  const scheduleDraft = () => {
    const draft = rows;
    storageWrite = storageWrite.catch(() => {}).then(() =>
      chromeApi.storage.local.set({studioDraft:draft, studioUnsynced:true}));
    storageWrite.catch(error => status(`Không lưu được catalog trong Chrome: ${error.message}`));
  };
  const merge = observations => {
    if (!Array.isArray(observations) || !observations.length) return;
    const signature = JSON.stringify(observations);
    if (signature === lastSnapshot) return;
    lastSnapshot = signature;
    rows = mergeCatalog(rows, observations);
    revision++; dirty = true; scheduleDraft(); render();
  };
  const listener = (message, sender) => {
    if (message?.type !== "bulk_scan_update" || sender?.tab?.id !== tabId ||
        message.session !== scanSession) return;
    merge(message.channels);
    status(message.complete ? "Đã dừng quét. Kiểm tra bảng rồi bấm Lưu và đồng bộ TE." :
      `Đang quét và chờ mục mới khi cuộn menu… đã thấy ${rows.length} bản ghi. ${message.switcher_open ? "Menu đang hiện." : "Chưa thấy menu chuyển kênh."}`);
  };
  chromeApi.runtime.onMessage.addListener(listener);
  async function activeTab() {
    const [tab] = await withTimeout(chromeApi.tabs.query({active:true,currentWindow:true}), 3000,
      "Không lấy được tab Studio sau 3 giây.");
    if (!tab?.url?.startsWith("https://studio.youtube.com/")) throw new Error("Mở tab YouTube Studio trước.");
    return tab;
  }
  async function start() {
    const tab = await activeTab(); tabId = tab.id;
    lastSnapshot = "";
    status("Đang đọc menu chuyển kênh; hãy mở và cuộn danh sách trong Studio…");
    const reply = await withTimeout(chromeApi.tabs.sendMessage(tabId,
      {type:"start_bulk_scan", duration_ms:defaults.duration || 20000}), 8000,
      "Content script không phản hồi sau 8 giây; tải lại tab Studio và thử quét lại.");
    if (!reply?.session) throw new Error(reply?.error || "Content script không phản hồi; tải lại tab Studio.");
    scanSession = reply.session;
    merge(reply.channels);
    render();
  }
  async function stop() {
    if (tabId !== null) await withTimeout(chromeApi.tabs.sendMessage(tabId,
      {type:"stop_bulk_scan"}), 3000, "Không thể dừng content script; phiên quét sẽ tự hết hạn.");
    scanSession = "";
    status("Đã dừng quét. Kiểm tra bảng rồi bấm Lưu và đồng bộ TE.");
  }
  async function save() {
    await storageWrite;
    const snapshot = rows, savedRevision = revision;
    await chromeApi.storage.local.set({studioCatalog:snapshot, studioDraft:snapshot, studioUnsynced:true});
    dirty = true; render();
    status("Đã giữ catalog trong Chrome; đang đồng bộ sang TE…");
    try {
      const reply = await nativeSync(chromeApi, snapshot, defaults.hostTimeout || 15000);
      await storageWrite;
      if (revision === savedRevision) {
        await chromeApi.storage.local.set({studioUnsynced:false, studioLastSyncedAt:new Date().toISOString()});
        dirty = false;
      }
      render();
      status(`Đã đồng bộ ${reply.count} bản ghi sang TE.` + (dirty ? " Có thay đổi mới cần lưu tiếp." : ""));
    } catch (error) { status(`Chưa đồng bộ TE: ${error.message}`); }
  }
  async function verify() {
    const candidate = rows.find(row => row.key === candidateKey && !row.id);
    if (!candidate) throw new Error("Chọn một ứng viên chưa có ID trong bảng trước.");
    const tab = await activeTab();
    const reply = await chromeApi.tabs.sendMessage(tab.id, {type:"scan_visible_studio"});
    const active = reply?.channels?.find(row => row.id && row.source_url === reply.page_url);
    if (!active) throw new Error("URL Studio của kênh đang mở chưa có Channel ID UC hợp lệ.");
    if (rows.some(row => row.id === active.id))
      throw new Error("ID này đã có trong catalog; không tự gộp ứng viên để tránh mất chỉnh sửa.");
    const approved = (defaults.confirm || window.confirm)(
      `Bạn đã đối chiếu ứng viên “${candidate.display_name || candidate.handle}” với kênh đang mở có ID ${active.id}?\nViệc liên kết này do bạn xác nhận; quyền upload vẫn chưa được xác minh.`);
    if (!approved) return;
    rows = markDuplicates(rows.map(row => row.key === candidateKey ? {...row,
      id:active.id, source_url:reply.page_url, id_source:"studio_url_user_confirmed",
      status:"identity_user_confirmed", last_seen_at:new Date().toISOString()} : row));
    candidateKey = ""; revision++; dirty = true; scheduleDraft(); render();
    status("Đã liên kết ID từ URL theo xác nhận của bạn. Bấm Lưu để đồng bộ TE.");
  }
  render();
  return {start, stop, save, verify, getRows:()=>rows, merge, render};
}

if (typeof document !== "undefined") {
  const ui = {status:document.getElementById("status"), counts:document.getElementById("counts"),
    rows:document.getElementById("rows")};
  createPanel(chrome,ui).then(panel => {
    for (const [id, action] of [["start",panel.start],["stop",panel.stop],["save",panel.save],["verify",panel.verify]]) {
      document.getElementById(id).addEventListener("click", () => action().catch(error => {
        ui.status.textContent = `Lỗi: ${error.message || error}`;
      }));
    }
  }).catch(error => { ui.status.textContent = `Không mở được catalog: ${error.message}`; });
}
if (typeof module !== "undefined") module.exports = {withTimeout, nativeSync, createPanel};
