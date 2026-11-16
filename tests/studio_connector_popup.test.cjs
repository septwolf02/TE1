const assert = require("node:assert/strict");
const {runScan, runDiagnostic, nativeError} = require("../studio_connector_extension/popup.js");

const A = "UC" + "a".repeat(22);
const url = `https://studio.youtube.com/channel/${A}/videos`;
const scan = {type:"scan_results", schema:1, page_url:url, page_url_after:url,
  channels:[{id:A, name:"Channel", source_url:url}]};

function event() {
  let callback;
  return {addListener(fn) { callback = fn; }, emit(value) { callback?.(value); }};
}

function chromeMock({dom = Promise.resolve(scan), native = "success"} = {}) {
  let disconnected = false;
  let sentPayload;
  const onMessage = event(), onDisconnect = event();
  const runtime = {lastError:null, connectNative(name) {
    assert.equal(name, "com.testudio.studio_connector");
    return {onMessage, onDisconnect, postMessage(payload) {
      sentPayload = payload;
      if (native === "success") setTimeout(() => {
        onMessage.emit({stage:"saving"});
        onMessage.emit({ok:true, count:1, verified_count:1, id_count:1,
          diagnostics:{active_id_found:true,active_name_found:true,switcher_detected:true,
            switcher_entries:2,switcher_names:2,verified_identities:1}});
      }, 0);
      if (native === "empty") setTimeout(() => onMessage.emit({ok:true, count:1, verified_count:0, id_count:0}), 0);
      if (native === "incomplete") setTimeout(() => onMessage.emit({ok:true, count:1, verified_count:0, id_count:1,
        diagnostics:{active_id_found:true,active_name_found:false,switcher_detected:true,
          switcher_entries:2,switcher_names:0,verified_identities:0}}), 0);
      if (native === "manual") setTimeout(() => onMessage.emit({ok:true, count:1, verified_count:0,
        user_named_count:1, id_count:1, diagnostics:{active_id_found:true,active_name_found:false,
          switcher_detected:false,switcher_entries:0,switcher_names:0,verified_identities:0}}), 0);
      if (native === "malformed") setTimeout(() => onMessage.emit({unexpected:true}), 0);
      if (native === "crash") setTimeout(() => {
        runtime.lastError = {message:"Native host has exited"};
        onDisconnect.emit();
      }, 0);
    }, disconnect() { disconnected = true; }};
  }};
  return {tabs:{query:async()=>[{id:1,url}], sendMessage:()=>dom}, runtime,
    wasDisconnected:()=>disconnected, sentPayload:()=>sentPayload};
}

(async () => {
  const progress = [];
  const success = chromeMock();
  await runScan(success, value => progress.push(value), {tab:50, dom:50, host:50});
  assert.deepEqual(progress.slice(0,3), ["Đang đọc giao diện Studio…",
    "Đang gửi dữ liệu đến Native Host…", "Đang lưu kết quả quét…"]);
  assert.match(progress.at(-1), /Hoàn tất/);
  assert.match(progress.at(-1), /mục menu: 2/);
  assert.equal(success.wasDisconnected(), true);

  const tabTimeout = chromeMock();
  tabTimeout.tabs.query = () => new Promise(()=>{});
  await assert.rejects(runScan(tabTimeout, ()=>{}, {tab:10, dom:20, host:20}),
    /Chrome không trả về tab/);
  await assert.rejects(runScan(chromeMock({dom:new Promise(()=>{})}), ()=>{},
    {tab:20, dom:10, host:20}), /Content script không phản hồi/);
  await assert.rejects(runScan(chromeMock({dom:Promise.reject(new Error("Could not establish connection"))}),
    ()=>{}, {tab:20, dom:20, host:20}), /Content script chưa có/);
  const timeout = chromeMock({native:"timeout"});
  await assert.rejects(runScan(timeout, ()=>{}, {tab:20, dom:20, host:10}), /Native Host không phản hồi/);
  assert.equal(timeout.wasDisconnected(), true);
  await assert.rejects(runScan(chromeMock({native:"crash"}), ()=>{},
    {tab:20, dom:20, host:20}), /Python dừng lỗi/);
  await assert.rejects(runScan(chromeMock({native:"malformed"}), ()=>{},
    {tab:20, dom:20, host:20}), /thông điệp không hợp lệ/);
  await assert.rejects(runScan(chromeMock({native:"empty"}), ()=>{},
    {tab:20, dom:20, host:20}), /Không tìm thấy Channel ID/);
  const incompleteProgress = [];
  await runScan(chromeMock({native:"incomplete"}), value=>incompleteProgress.push(value),
    {tab:20, dom:20, host:20});
  assert.match(incompleteProgress.at(-1), /danh tính chưa hoàn chỉnh/);
  const manualScan = {...scan, channels:[{id:A, name:"", source_url:url}]};
  const manualChrome = chromeMock({dom:Promise.resolve(manualScan), native:"manual"});
  const manualProgress = [];
  await runScan(manualChrome, value=>manualProgress.push(value), {tab:20,dom:20,host:20}, "大谷翔平速報");
  assert.match(manualProgress.at(-1), /tên nhập tay/);
  assert.equal(manualChrome.sentPayload().manual_name, "大谷翔平速報");
  await assert.rejects(runScan(chromeMock({dom:Promise.resolve(manualScan)}), ()=>{},
    {tab:20,dom:20,host:20}, "   "), /Tên kênh nhập tay/);
  const diagnosticChrome = chromeMock({dom:Promise.resolve({active_id_found:true,candidates:[{tag:"ytcp-channel-info"}]})});
  assert.equal((await runDiagnostic(diagnosticChrome)).candidates[0].tag, "ytcp-channel-info");
  assert.match(nativeError("Specified native messaging host not found"), /Không tìm thấy Native Host/);
  assert.match(nativeError("Access to the specified native messaging host is forbidden"), /Extension ID/);
  assert.match(nativeError("Failed to start native messaging host"), /Không khởi chạy/);
})().catch(error => { console.error(error); process.exitCode = 1; });
