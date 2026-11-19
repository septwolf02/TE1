"use strict";

function withTimeout(promise, ms, message) {
  let timer;
  return Promise.race([promise, new Promise((_, reject) => {
    timer = setTimeout(() => reject(new Error(message)), ms);
  })]).finally(() => clearTimeout(timer));
}

function nativeRequest(chromeApi, payload, timeoutMs = 15000) {
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
      timer = setTimeout(() => finish(new Error(`Native Host quá hạn ${Math.ceil(timeoutMs / 1000)} giây; kiểm tra trạng thái trong TE trước khi thử lại.`)), timeoutMs);
      port.postMessage(payload);
    } catch (error) { finish(new Error("Không mở được Native Host. Catalog vẫn ở Chrome; kiểm tra cài đặt và Extension ID.")); }
  });
}

function nativeSync(chromeApi, records, timeoutMs = 15000) {
  return nativeRequest(chromeApi, {type:"catalog_sync", schema:1, records}, timeoutMs);
}

async function processUploadRequest(chromeApi, request, report, options = {}) {
  if (!request?.pending || !/^[a-f0-9]{32}$/.test(request.request_id || "") ||
      !/^UC[A-Za-z0-9_-]{22}$/.test(request.channel_id || ""))
    throw new Error("Yêu cầu upload từ TE không hợp lệ.");
  const timeout = options.hostTimeout || 15000;
  const native = payload => nativeRequest(chromeApi, {schema:1,...payload},
    payload.type === "upload_claim" ? 300000 : timeout);
  let selected = false;
  let browserStep = "opening_studio";
  try {
    const channelId = request.channel_id;
    const studioUrl = `https://studio.youtube.com/channel/${channelId}/videos`;
    const tabs = await chromeApi.tabs.query({url:"https://studio.youtube.com/*"});
    let tab = tabs.find(item => channelFromStudioUrl(item.url) === channelId);
    if (tab) tab = await chromeApi.tabs.update(tab.id,{active:true});
    else tab = await chromeApi.tabs.create({url:studioUrl,active:true});
    report(`Đang mở Studio cho Channel ID ${channelId}…`);
    let evidence;
    for (let attempt = 0; attempt < 40; attempt++) {
      try {
        evidence = await chromeApi.tabs.sendMessage(tab.id,{type:"scan_visible_studio"});
        if (evidence?.page_url === evidence?.page_url_after &&
            channelFromStudioUrl(evidence.page_url) === channelId) break;
        if (evidence?.page_url && channelFromStudioUrl(evidence.page_url) &&
            channelFromStudioUrl(evidence.page_url) !== channelId)
          throw new Error("Channel ID trong Studio khác kênh TE đã chọn.");
      } catch (error) {
        if (/khác kênh TE/.test(error.message)) throw error;
      }
      evidence = null;
      await new Promise(resolve=>setTimeout(resolve,500));
    }
    if (!evidence) throw new Error("Studio không mở đúng Channel ID trong 20 giây.");
    browserStep = "channel_verified";
    const claimed = await native({type:"upload_claim",request_id:request.request_id,
      page_url:evidence.page_url,page_url_after:evidence.page_url_after});
    const file = {...claimed.file,channel_id:channelId};
    if (file.request_id !== request.request_id || file.channel_id !== channelId)
      throw new Error("Native Host trả về tệp cho sai yêu cầu hoặc kênh.");
    report("Đã đối chiếu Channel ID; đang tìm ô chọn MP4 Studio…");
    browserStep = "waiting_for_upload_dialog";
    const receipt = await selectStudioFile(chromeApi,tab.id,file,async()=>{
      await native({type:"upload_started",request_id:request.request_id});
      selected = true;
      browserStep = "selecting_file";
    });
    await native({type:"upload_selected",request_id:request.request_id,
      filename:receipt.filename,bytes:receipt.bytes});
    report(`Chrome đã nhận ${receipt.filename} (${receipt.bytes} byte). ` +
      "Kiểm tra tiến trình trong Studio; chưa xác nhận video được tạo hoặc ở chế độ Private.");
    return {...receipt,tabId:tab.id};
  } catch (error) {
    const reason = selected ? (error.code || "chrome_disconnected") :
      /Channel ID|sai yêu cầu|sai.*kênh/.test(error.message) ? "channel_mismatch" :
      error.code || (/ô chọn/.test(error.message) ? "missing_input" : "debugger_failure");
    const diagnostics=error.diagnostics || {browser_step:browserStep,dialog_open:false,
      candidate_count:0,rejected:[],cdp_attached:false};
    try { await native({type:"upload_failed",request_id:request.request_id,reason,diagnostics}); }
    catch (_) { /* TE recovery conservatively marks an uncertain attempt. */ }
    throw error;
  }
}

async function createPanel(chromeApi, ui, defaults = {}) {
  let rows = [], scanSession = "", tabId = null, dirty = false, revision = 0,
      storageWrite = Promise.resolve(),
      candidateKey = "", lastSnapshot = "", syncedCount = 0, diagnosticReport = "",
      targetTabId = null, proposals = [], selectionRequest = null;
  const observedThisScan = new Set();
  const saved = await chromeApi.storage.local.get(["studioCatalog", "studioDraft", "studioUnsynced",
    "studioLastSyncedCount"]);
  syncedCount = Number.isSafeInteger(saved.studioLastSyncedCount) ? saved.studioLastSyncedCount : 0;
  rows = Array.isArray(saved.studioDraft) ? saved.studioDraft : Array.isArray(saved.studioCatalog) ? saved.studioCatalog : [];
  dirty = !!saved.studioUnsynced || JSON.stringify(saved.studioCatalog || []) !== JSON.stringify(rows);
  const status = message => { ui.status.textContent = message; };
  const pullCatalog = async () => {
    const reply = await nativeRequest(chromeApi, {type:"catalog_get",schema:1}, defaults.hostTimeout || 15000);
    if (!Array.isArray(reply.records)) throw new Error("Native Host không trả về catalog hợp lệ.");
    rows = mergeRemoteCatalog(rows, reply.records);
    await chromeApi.storage.local.set({studioCatalog:rows, studioDraft:rows, studioUnsynced:dirty});
    render();
    return reply.count;
  };
  const render = () => {
    ui.rows.replaceChildren();
    ui.counts.textContent = `Mục menu lượt quét này: ${observedThisScan.size} • ` +
      `Catalog tích lũy: ${rows.length} • ID có chứng cứ Studio: ${rows.filter(r=>r.id &&
        ["studio_url", "studio_link", "studio_url_user_confirmed"].includes(r.id_source)).length} • ` +
      `ID nhập tay: ${rows.filter(r=>r.id_source === "user_supplied").length} • ` +
      `Ứng viên thiếu ID: ${rows.filter(r=>!r.id).length} • ` +
      `Bản ghi ở TE sau lần đồng bộ gần nhất: ${syncedCount} • ` +
      (dirty ? "Chưa đồng bộ thay đổi mới" : "Đã đồng bộ catalog hiện tại");
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
      if (!row.id || row.id_source === "user_supplied") {
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
    if (scanSession) for (const item of observations) if (item.evidence === "visible_switcher_item")
      observedThisScan.add(JSON.stringify([item.id, item.handle, item.name, item.entry_index]));
    const signature = JSON.stringify(observations);
    if (signature === lastSnapshot) return;
    lastSnapshot = signature;
    rows = mergeCatalog(rows, observations);
    revision++; dirty = true; scheduleDraft(); render();
  };
  const listener = (message, sender) => {
    if (sender?.tab?.id === targetTabId && message?.type === "target_region_proposed") {
      if (message.error) { status(message.error); return; }
      proposals = message.candidates || [];
      if (ui.regionChoices) {
        ui.regionChoices.replaceChildren();
        proposals.forEach((item, index) => {
          const option = document.createElement("option"); option.value = String(index);
          option.textContent = `${item.container.tag}#${item.container.id || ""} — ${item.visible_matching_rows} hàng cùng dạng`;
          ui.regionChoices.append(option);
        });
      }
      status(`Đã tô vùng đề xuất với ${proposals[0]?.visible_matching_rows || 0} hàng cùng dạng. Đối chiếu vùng tô sáng trong Studio, chọn vùng khác nếu cần, rồi Xác nhận hoặc Hủy.`);
      return;
    }
    if (sender?.tab?.id === targetTabId && message?.type === "target_region_cancelled") {
      proposals = []; status(message.reason || "Đã hủy chọn vùng."); return;
    }
    if (message?.type !== "bulk_scan_update" || sender?.tab?.id !== tabId ||
        message.session !== scanSession) return;
    if (message.error) { scanSession = ""; status(`Vùng quét không còn: ${message.error}`); return; }
    merge(message.channels);
    if (message.diagnostics) {
      diagnosticReport = JSON.stringify(message.diagnostics, null, 2);
      if (ui.diagnostic) ui.diagnostic.textContent = diagnosticReport;
    }
    status(message.complete ? "Đã dừng quét. Kiểm tra bảng rồi bấm Lưu và đồng bộ TE." :
      `Đang quét và chờ mục mới khi cuộn menu… thấy ${observedThisScan.size} mục menu lượt này. ${message.switcher_open ? "Menu đang hiện." : "Chưa thấy menu chuyển kênh."}`);
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
    lastSnapshot = ""; observedThisScan.clear(); render();
    status("Đang đọc menu chuyển kênh; hãy mở và cuộn danh sách trong Studio…");
    const reply = await withTimeout(chromeApi.tabs.sendMessage(tabId,
      {type:"start_bulk_scan", duration_ms:defaults.duration || 20000}), 8000,
      "Content script không phản hồi sau 8 giây; tải lại tab Studio và thử quét lại.");
    if (!reply?.session) throw new Error(reply?.error || "Content script không phản hồi; tải lại tab Studio.");
    scanSession = reply.session;
    merge(reply.channels);
    render();
  }
  async function target() {
    const tab = await activeTab(); targetTabId = tab.id; proposals = [];
    const reply = await withTimeout(chromeApi.tabs.sendMessage(tab.id,
      {type:"begin_region_targeting"}), 8000, "Không bật được chế độ chọn vùng.");
    if (!reply?.ok) throw new Error(reply?.error || "Không bật được chế độ chọn vùng.");
    status("Chế độ chọn vùng đang bật trong 30 giây. Mở menu kênh rồi nhấp một hàng kênh; cú nhấp này sẽ không chuyển kênh. Esc hoặc Hủy để thoát.");
  }
  async function cancelTarget() {
    if (targetTabId !== null) await withTimeout(chromeApi.tabs.sendMessage(targetTabId,
      {type:"cancel_region_targeting"}), 3000, "Không liên lạc được tab; chế độ chọn sẽ tự hết hạn.");
    proposals = []; status("Đã hủy chế độ chọn vùng.");
  }
  async function propose() {
    if (!proposals.length) return;
    const index = Number(ui.regionChoices?.value || 0);
    const reply = await chromeApi.tabs.sendMessage(targetTabId,{type:"propose_region",index});
    if (!reply?.ok) throw new Error(reply?.error || "Không tô được vùng đề xuất.");
    status(`Đang tô vùng ${index + 1}. Chỉ xác nhận nếu đó là danh sách kênh trong menu chuyển kênh.`);
  }
  async function confirmTarget() {
    if (!proposals.length) throw new Error("Hãy nhấp một hàng kênh trong Studio trước.");
    const index = Number(ui.regionChoices?.value || 0);
    const reply = await chromeApi.tabs.sendMessage(targetTabId,{type:"confirm_region",index});
    if (!reply?.ok) throw new Error(reply?.error || "Không xác nhận được vùng kênh.");
    diagnosticReport = JSON.stringify(reply.diagnostic, null, 2);
    if (ui.diagnostic) ui.diagnostic.textContent = diagnosticReport;
    proposals = [];
    status(`Đã xác nhận vùng có ${reply.diagnostic.visible_matching_rows} hàng cùng dạng. Bấm Quét rồi cuộn menu để tích lũy thêm hàng.`);
  }
  async function stop() {
    if (tabId !== null) await withTimeout(chromeApi.tabs.sendMessage(tabId,
      {type:"stop_bulk_scan"}), 3000, "Không thể dừng content script; phiên quét sẽ tự hết hạn.");
    scanSession = "";
    status("Đã dừng quét. Kiểm tra bảng rồi bấm Lưu và đồng bộ TE.");
  }
  async function save() {
    await storageWrite;
    await chromeApi.storage.local.set({studioCatalog:rows, studioDraft:rows, studioUnsynced:true});
    dirty = true; render();
    try { await pullCatalog(); }
    catch (error) {
      status(`Chưa thể đồng bộ: ${error.message}. Catalog trong Chrome vẫn được giữ.`);
      return;
    }
    const snapshot = rows, savedRevision = revision;
    status("Đã giữ catalog trong Chrome; đang đồng bộ sang TE…");
    try {
      const reply = await nativeSync(chromeApi, snapshot, defaults.hostTimeout || 15000);
      await storageWrite;
      if (revision === savedRevision) {
        await chromeApi.storage.local.set({studioUnsynced:false, studioLastSyncedAt:new Date().toISOString(),
          studioLastSyncedCount:reply.count});
        dirty = false;
      }
      syncedCount = reply.count;
      render();
      status(`Đã đồng bộ ${reply.count} bản ghi sang TE.` + (dirty ? " Có thay đổi mới cần lưu tiếp." : ""));
    } catch (error) { status(`Chưa đồng bộ TE: ${error.message}`); }
  }
  async function verify() {
    const candidate = rows.find(row => row.key === candidateKey &&
      (!row.id || row.id_source === "user_supplied"));
    if (!candidate) throw new Error("Chọn ứng viên thiếu ID hoặc có ID nhập tay trong bảng trước.");
    const tab = await activeTab();
    const reply = await chromeApi.tabs.sendMessage(tab.id, {type:"scan_visible_studio"});
    const active = reply?.channels?.find(row => row.id && row.id_source === "studio_url" &&
      reply.page_url?.startsWith(`https://studio.youtube.com/channel/${row.id}`));
    if (!active) throw new Error("URL Studio của kênh đang mở chưa có Channel ID UC hợp lệ.");
    if (candidate.id && candidate.id !== active.id)
      throw new Error("ID trên URL Studio khác ID nhập tay; hãy sửa ID trong TE trước.");
    if (rows.some(row => row.key !== candidate.key && row.id === active.id))
      throw new Error("ID này đã có trong catalog; không tự gộp ứng viên để tránh mất chỉnh sửa.");
    const approved = (defaults.confirm || window.confirm)(
      `Bạn đã đối chiếu ứng viên “${candidate.display_name || candidate.handle}” với kênh đang mở có ID ${active.id}?\nViệc liên kết này do bạn xác nhận; quyền upload vẫn chưa được xác minh.`);
    if (!approved) return;
    rows = markDuplicates(rows.map(row => row.key === candidateKey ? {...row,
      id:active.id, source_url:`https://studio.youtube.com/channel/${active.id}`,
      id_source:"studio_url_user_confirmed",
      status:"identity_user_confirmed", last_seen_at:new Date().toISOString()} : row));
    candidateKey = ""; revision++; dirty = true; scheduleDraft(); render();
    status("Đã liên kết ID từ URL theo xác nhận của bạn. Bấm Lưu để đồng bộ TE.");
  }
  async function receiveSelection() {
    const reply = await nativeRequest(chromeApi, {type:"selection_get",schema:1},
      defaults.hostTimeout || 15000);
    const request = reply.request;
    if (!request?.pending || !/^[a-f0-9]{32}$/.test(request.request_id || "")) {
      selectionRequest = null;
      ui.selectionRequest.textContent = "Không có yêu cầu còn hiệu lực từ TE. Chọn kênh trong TE rồi bấm Mở và xác minh.";
      return;
    }
    await pullCatalog();
    if (!rows.some(row => row.key === request.key && row.id === request.id))
      throw new Error("Catalog trong Chrome và yêu cầu TE không khớp; hãy chọn lại kênh trong TE.");
    selectionRequest = request;
    ui.selectionRequest.textContent = `TE chọn: ${request.name || "(chưa có tên)"} ` +
      `${request.handle || ""} • ID: ${request.id || "chưa có"} • nguồn: ${request.id_source}. ` +
      "Đối chiếu URL Studio đang mở trước khi xác nhận. Quyền upload chưa xác minh.";
    if (request.id && ["studio_url", "studio_link", "studio_url_user_confirmed"].includes(request.id_source)) {
      const current = await activeTab().catch(() => null);
      if (!current?.url?.startsWith(`https://studio.youtube.com/channel/${request.id}`)) {
        await withTimeout(chromeApi.tabs.create({url:`https://studio.youtube.com/channel/${request.id}`}),
          5000, "Không mở được tab Studio trong 5 giây.");
        status("Đã mở tab Studio theo ID có chứng cứ. Chờ trang tải, kiểm tra kênh và bấm Đối chiếu.");
      } else status("Tab Studio đang mở có ID dự kiến. Kiểm tra kênh rồi bấm Đối chiếu.");
    } else {
      status("ID chưa xác minh hoặc còn thiếu. Hãy tự mở đúng kênh trong Studio, rồi bấm Đối chiếu.");
    }
  }
  async function confirmSelection() {
    const request = selectionRequest;
    if (!request) throw new Error("Bấm Nhận yêu cầu từ TE trước.");
    const tab = await activeTab();
    const reply = await withTimeout(chromeApi.tabs.sendMessage(tab.id, {type:"scan_visible_studio"}),
      8000, "Content script không phản hồi; tải lại tab Studio.");
    if (!reply || reply.error || reply.page_url !== reply.page_url_after)
      throw new Error(reply?.error || "Trang Studio đã đổi trong lúc đối chiếu; thử lại.");
    let observedId = "";
    try {
      const url = new URL(reply.page_url);
      observedId = url.protocol === "https:" && url.hostname === "studio.youtube.com" ?
        (url.pathname.match(/^\/channel\/(UC[A-Za-z0-9_-]{22})(?:\/|$)/) || [])[1] || "" : "";
    } catch (_) { /* invalid URL */ }
    if (!observedId) throw new Error("Tab Studio đang mở không có Channel ID UC hợp lệ.");
    if (request.id && request.id !== observedId)
      throw new Error(`ID URL ${observedId} khác ID ${request.id} đã chọn trong TE. Không liên kết.`);
    if (rows.some(row => row.key !== request.key && row.id === observedId))
      throw new Error("ID URL đã thuộc bản ghi khác trong catalog; không tự gộp theo tên.");
    const observedName = reply.channels?.find(row => row.id === observedId && row.id_source === "studio_url")?.name || "";
    const approved = (defaults.confirm || window.confirm)(
      `TE chọn: ${request.name || "(chưa có tên)"} ${request.handle || ""}\n` +
      `Studio đang mở: ${observedName || "(không đọc được tên)"} — ${observedId}\n` +
      "Bạn đã tự đối chiếu đúng kênh và đồng ý liên kết ID URL này? Quyền upload vẫn chưa xác minh.");
    if (!approved) return;
    const confirmed = await nativeRequest(chromeApi, {type:"selection_confirm",schema:1,
      request_id:request.request_id,page_url:reply.page_url,page_url_after:reply.page_url_after,
      observed_name:observedName}, defaults.hostTimeout || 15000);
    if (confirmed.key !== request.key || confirmed.channel_id !== observedId)
      throw new Error("Native Host trả về danh tính không khớp yêu cầu.");
    selectionRequest = null;
    await pullCatalog();
    ui.selectionRequest.textContent = `Đã xác minh URL Studio cho ${request.name || "(chưa có tên)"} — ${observedId}.`;
    status("Đã lưu chứng cứ URL trong TE và đồng bộ về Side Panel. Chưa xác minh quyền upload.");
  }
  async function diagnose() {
    const tab = await activeTab();
    status("Đang đọc cấu trúc menu trong 3 giây; hãy cuộn menu nếu có thể…");
    const report = await withTimeout(chromeApi.tabs.sendMessage(tab.id,
      {type:"diagnose_channel_menu"}), 8000,
      "Chẩn đoán quá hạn; tải lại tab Studio và thử lại khi menu đang mở.");
    if (!report?.first || !report?.last) throw new Error(report?.error ||
      "Content script không trả về chẩn đoán; tải lại tab Studio.");
    diagnosticReport = JSON.stringify(report, null, 2);
    if (ui.diagnostic) ui.diagnostic.textContent = diagnosticReport;
    status("Đã hiển thị báo cáo cấu trúc. Kiểm tra nội dung trước khi sao chép và gửi lại.");
  }
  async function copyDiagnostic() {
    if (!diagnosticReport) throw new Error("Hãy chạy Chẩn đoán menu kênh trước.");
    await (defaults.clipboard || navigator.clipboard).writeText(diagnosticReport);
    status("Đã sao chép báo cáo đã lọc. Hãy xem lại nội dung trước khi chia sẻ.");
  }
  render();
  try { await pullCatalog(); }
  catch (error) { status(`Chưa đọc được catalog từ TE: ${error.message}. Dữ liệu Chrome vẫn được giữ.`); }
  return {start, stop, save, verify, receiveSelection, confirmSelection, diagnose, copyDiagnostic,
    target, cancelTarget, propose, confirmTarget, pullCatalog,
    getRows:()=>rows, merge, render};
}

if (typeof document !== "undefined") {
  const ui = {status:document.getElementById("status"), counts:document.getElementById("counts"),
    rows:document.getElementById("rows"), diagnostic:document.getElementById("diagnostic"),
    selectionRequest:document.getElementById("selectionRequest"),
    regionChoices:document.getElementById("regionChoices")};
  createPanel(chrome,ui).then(panel => {
    for (const [id, action] of [["start",panel.start],["stop",panel.stop],["save",panel.save],
      ["verify",panel.verify],["receiveSelection",panel.receiveSelection],
      ["confirmSelection",panel.confirmSelection],
      ["diagnose",panel.diagnose],["copyDiagnostic",panel.copyDiagnostic],
      ["target",panel.target],["cancelTarget",panel.cancelTarget],
      ["confirmTarget",panel.confirmTarget],["pullCatalog",panel.pullCatalog]]) {
      document.getElementById(id).addEventListener("click", () => action().catch(error => {
        ui.status.textContent = `Lỗi: ${error.message || error}`;
      }));
    }
    ui.regionChoices.addEventListener("change", () => panel.propose().catch(error => {
      ui.status.textContent = `Lỗi: ${error.message || error}`;
    }));
    window.addEventListener("pagehide", () => panel.cancelTarget().catch(() => {}));
  }).catch(error => { ui.status.textContent = `Không mở được catalog: ${error.message}`; });
}
if (typeof module !== "undefined") module.exports = {withTimeout, nativeRequest, nativeSync,
  processUploadRequest, createPanel};
