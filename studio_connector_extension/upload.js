"use strict";

const STUDIO_CHANNEL = /^https:\/\/studio\.youtube\.com\/channel\/(UC[A-Za-z0-9_-]{22})(?:[/?#]|$)/;

function channelFromStudioUrl(url) {
  return typeof url === "string" ? (url.match(STUDIO_CHANNEL) || [])[1] || "" : "";
}

function fileInputs(node, found = []) {
  if (!node || typeof node !== "object") return found;
  const attributes = {};
  for (let index = 0; index < (node.attributes || []).length; index += 2)
    attributes[node.attributes[index].toLowerCase()] = node.attributes[index + 1] || "";
  if (node.nodeName?.toUpperCase() === "INPUT" && attributes.type?.toLowerCase() === "file" &&
      /video|\.mp4/i.test(attributes.accept || "")) found.push(node.nodeId);
  for (const child of [...(node.children || []), ...(node.shadowRoots || []), ...(node.contentDocument ?
    [node.contentDocument] : [])]) fileInputs(child, found);
  return found;
}

async function selectStudioFile(chromeApi, tabId, file, beforeSelection) {
  if (!file || typeof file.path !== "string" || !/\.mp4$/i.test(file.path) ||
      !Number.isSafeInteger(file.bytes) || file.bytes < 12 ||
      file.filename !== file.path.split(/[\\/]/).pop()) throw new Error("MP4 Native Host không hợp lệ.");
  const target = {tabId};
  let attached = false;
  try {
    await chromeApi.debugger.attach(target, "1.3");
    attached = true;
    const send = (method, params = {}) => chromeApi.debugger.sendCommand(target, method, params);
    const tree = await send("DOM.getDocument", {depth:-1, pierce:true});
    const nodes = fileInputs(tree.root);
    if (nodes.length !== 1 || !nodes[0]) throw new Error("Không tìm thấy đúng một ô chọn video Studio.");
    const current = await chromeApi.tabs.get(tabId);
    if (channelFromStudioUrl(current.url) !== file.channel_id)
      throw new Error("Channel ID đã đổi trước khi chọn MP4.");
    await beforeSelection(); // Persist an uncertain attempt before browser I/O.
    await send("DOM.setFileInputFiles", {nodeId:nodes[0], files:[file.path]});
    const after = await chromeApi.tabs.get(tabId);
    if (channelFromStudioUrl(after.url) !== file.channel_id)
      throw new Error("Channel ID đã đổi khi Chrome nhận MP4; cần kiểm tra Studio.");
    const resolved = await send("DOM.resolveNode", {nodeId:nodes[0]});
    const receipt = await send("Runtime.callFunctionOn", {objectId:resolved.object.objectId,
      functionDeclaration:"function(){const f=this.files && this.files[0]; return f ? {name:f.name,size:f.size} : null}",
      returnByValue:true});
    const value = receipt?.result?.value;
    if (value?.name !== file.filename || value?.size !== file.bytes)
      throw new Error("Chrome nhận sai MP4 hoặc file input không cập nhật.");
    return {filename:value.name, bytes:value.size};
  } finally {
    if (attached) await chromeApi.debugger.detach(target);
  }
}

if (typeof module !== "undefined") module.exports = {channelFromStudioUrl, fileInputs, selectStudioFile};
