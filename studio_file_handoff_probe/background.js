"use strict";

function validProbeRequest(request) {
  return request?.type === "probe_file_input" &&
    typeof request.path === "string" && request.path.length < 2048 &&
    (/^[A-Za-z]:\\[^\r\n\0]+\.mp4$/i.test(request.path) || /^\/[^\r\n\0]+\.mp4$/i.test(request.path)) &&
    Number.isSafeInteger(request.expectedSize) && request.expectedSize >= 12 &&
    request.expectedSize <= 256 * 1024 ** 3;
}

async function probeFileInput(chromeApi, request) {
  if (!validProbeRequest(request)) throw new Error("Chọn đường dẫn MP4 tuyệt đối và kích thước hợp lệ.");
  const [tab] = await chromeApi.tabs.query({active:true,currentWindow:true});
  if (!tab?.id || !/^http:\/\/127\.0\.0\.1:\d+\/$/.test(tab.url || ""))
    throw new Error("Chỉ dùng trang thử nghiệm http://127.0.0.1:<port>/ đang mở.");
  const target = {tabId:tab.id};
  let attached = false;
  try {
    await chromeApi.debugger.attach(target,"1.3");
    attached = true;
    const send = (method,params={}) => chromeApi.debugger.sendCommand(target,method,params);
    const root = await send("DOM.getDocument");
    const node = await send("DOM.querySelector",{nodeId:root.root.nodeId,selector:"#probe-video"});
    if (!node.nodeId) throw new Error("Không thấy file input của trang thử nghiệm.");
    // CDP receives a local path. The MP4 bytes are not sent through Native Messaging or JS.
    await send("DOM.setFileInputFiles",{nodeId:node.nodeId,files:[request.path]});
    const value = await send("Runtime.evaluate",{expression:
      "(() => { const f = document.querySelector('#probe-video')?.files?.[0]; " +
      "return f ? {name:f.name,size:f.size} : null; })()",returnByValue:true});
    const receipt = value?.result?.value;
    const expectedName = request.path.split(/[\\/]/).pop();
    if (!receipt || receipt.name !== expectedName || receipt.size !== request.expectedSize)
      throw new Error("Tên hoặc kích thước file trong Chrome không khớp MP4 đã chọn.");
    return {received:true,filename:receipt.name,bytes:receipt.size};
  } finally {
    if (attached) await chromeApi.debugger.detach(target);
  }
}

if (typeof chrome !== "undefined" && chrome.runtime?.onMessage) {
  chrome.runtime.onMessage.addListener((request,sender,respond) => {
    if (request?.type !== "probe_file_input") return;
    if (sender.id !== chrome.runtime.id || sender.url !== chrome.runtime.getURL("popup.html")) {
      respond({ok:false,error:"Chỉ popup của extension thử nghiệm được gọi lệnh này."});
      return;
    }
    probeFileInput(chrome,request).then(result=>respond({ok:true,...result}),
      error=>respond({ok:false,error:String(error.message || error)}));
    return true;
  });
}

if (typeof module !== "undefined") module.exports = {validProbeRequest,probeFileInput};
