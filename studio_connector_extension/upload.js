"use strict";

const STUDIO_CHANNEL = /^https:\/\/studio\.youtube\.com\/channel\/(UC[A-Za-z0-9_-]{22})(?:[/?#]|$)/;

function channelFromStudioUrl(url) {
  return typeof url === "string" ? (url.match(STUDIO_CHANNEL) || [])[1] || "" : "";
}

function attrs(node) {
  const result={};
  for(let i=0;i<(node.attributes||[]).length;i+=2)
    result[node.attributes[i].toLowerCase()]=node.attributes[i+1]||"";
  return result;
}

function walk(node, ancestors=[], found=[]) {
  if(!node || typeof node!=="object") return found;
  const chain=[...ancestors,node]; found.push({node,chain});
  for(const child of [...(node.children||[]),...(node.shadowRoots||[]),
      ...(node.contentDocument?[node.contentDocument]:[])]) walk(child,chain,found);
  return found;
}

function isDialog(node) {
  const a=attrs(node);
  return a.role==="dialog" || a["aria-modal"]==="true" ||
    /(?:^|-)DIALOG$/.test(node.nodeName?.toUpperCase()||"");
}

function isUploadDialog(node) {
  if(!isDialog(node)) return false;
  const a=attrs(node);
  const identity=(node.nodeName||"")+" "+(a["aria-label"]||a.title||"");
  return /upload|アップロード|tải video/i.test(identity);
}

function inspectFileInputs(root) {
  const candidates=[],rejected=[];
  for(const {node,chain} of walk(root)) {
    if(node.nodeName?.toUpperCase()==="IFRAME" && !node.contentDocument)
      rejected.push("inaccessible_iframe");
    const a=attrs(node);
    if(node.nodeName?.toUpperCase()!=="INPUT" || a.type?.toLowerCase()!=="file") continue;
    const dialog=chain.some(isUploadDialog), accept=(a.accept||"").toLowerCase();
    const video=/video|\.mp4|\.mov|\.mkv|\.webm/.test(accept);
    const hidden=chain.some(item=>"hidden" in attrs(item)||attrs(item)["aria-hidden"]==="true");
    const reason=hidden?"hidden":/image|audio/.test(accept)&&!video?"non_video_accept":
      !dialog?"outside_upload_dialog":"";
    if(reason) rejected.push(reason);
    candidates.push({nodeId:node.nodeId,reason});
  }
  return {candidates,eligible:candidates.filter(item=>!item.reason),
    rejected:[...new Set(rejected)],dialogOpen:walk(root).some(({node})=>isUploadDialog(node))};
}

function fileInputs(node) {return inspectFileInputs(node).eligible.map(item=>item.nodeId);}

function uploadAction(root,clicked) {
  // Use exact accessible control names; never search arbitrary page text.
  for(const name of ["upload videos","upload video","動画をアップロード","動画のアップロード",
      "tải video lên","create","作成","tạo"]) {
    if(clicked.has(name)) continue;
    const matches=walk(root).filter(({node})=>{
      const a=attrs(node);
      return (node.nodeName?.toUpperCase()==="BUTTON"||a.role==="button") &&
        !a.disabled && a["aria-hidden"]!=="true" &&
        (a["aria-label"]||a.title||"").trim().toLowerCase()===name;
    });
    if(matches.length===1) return {nodeId:matches[0].node.nodeId,name};
    if(matches.length>1) return null;
  }
  return null;
}

async function selectStudioFile(chromeApi, tabId, file, beforeSelection, options={}) {
  if (!file || typeof file.path !== "string" || !/\.mp4$/i.test(file.path) ||
      !Number.isSafeInteger(file.bytes) || file.bytes < 12 ||
      file.filename !== file.path.split(/[\\/]/).pop()) throw new Error("MP4 Native Host không hợp lệ.");
  const target = {tabId};
  let attached = false;
  const diagnostics={browser_step:"cdp_attach",dialog_open:false,candidate_count:0,
    rejected:[],cdp_attached:false};
  try {
    try {await chromeApi.debugger.attach(target, "1.3");}
    catch(error) {error.code="debugger_failure";throw error;}
    attached = true;
    diagnostics.cdp_attached=true;
    const send = (method, params = {}) => {
      let timer;
      return Promise.race([chromeApi.debugger.sendCommand(target,method,params),
        new Promise((_,reject)=>{timer=setTimeout(()=>reject(new Error(`CDP ${method} quá hạn 10 giây.`)),10000)})])
        .finally(()=>clearTimeout(timer));
    };
    const clicked=new Set(), deadline=Date.now()+(options.waitMs??20000);
    const pause=options.pause||((ms)=>new Promise(resolve=>setTimeout(resolve,ms)));
    let nodeId;
    do {
      diagnostics.browser_step="waiting_for_upload_dialog";
      const tree=await send("DOM.getDocument",{depth:-1,pierce:true});
      const found=inspectFileInputs(tree.root);
      diagnostics.dialog_open=found.dialogOpen;
      diagnostics.candidate_count=found.candidates.length;
      diagnostics.rejected=found.rejected;
      if(found.eligible.length===1) {nodeId=found.eligible[0].nodeId;break;}
      if(found.eligible.length>1) diagnostics.rejected=[...found.rejected,"ambiguous_inputs"];
      if(!found.dialogOpen) {
        const action=uploadAction(tree.root,clicked);
        if(action) {
          const resolved=await send("DOM.resolveNode",{nodeId:action.nodeId});
          await send("Runtime.callFunctionOn",{objectId:resolved.object.objectId,
            functionDeclaration:"function(){this.click()}",returnByValue:true});
          clicked.add(action.name);
          diagnostics.browser_step="opening_upload_dialog";
        }
      }
      if(Date.now()>=deadline) break;
      await pause(options.pollMs??500);
    } while(true);
    if(!nodeId) {
      if(clicked.size && !diagnostics.dialog_open)
        diagnostics.browser_step="upload_dialog_action_sent_but_not_open";
      const error=new Error("YouTube Studio đã mở nhưng chưa tìm được ô chọn video. Kiểm tra giao diện upload hoặc thử lại sau khi khắc phục.");
      error.code="missing_input";throw error;
    }
    const current = await chromeApi.tabs.get(tabId);
    if (channelFromStudioUrl(current.url) !== file.channel_id)
      throw new Error("Channel ID đã đổi trước khi chọn MP4.");
    diagnostics.browser_step="selecting_file";
    await beforeSelection(); // Persist an uncertain attempt before browser I/O.
    await send("DOM.setFileInputFiles", {nodeId, files:[file.path]});
    const after = await chromeApi.tabs.get(tabId);
    if (channelFromStudioUrl(after.url) !== file.channel_id)
      throw new Error("Channel ID đã đổi khi Chrome nhận MP4; cần kiểm tra Studio.");
    const resolved = await send("DOM.resolveNode", {nodeId});
    const receipt = await send("Runtime.callFunctionOn", {objectId:resolved.object.objectId,
      functionDeclaration:"function(){const f=this.files && this.files[0]; return f ? {name:f.name,size:f.size} : null}",
      returnByValue:true});
    const value = receipt?.result?.value;
    if (value?.name !== file.filename || value?.size !== file.bytes)
      throw new Error("Chrome nhận sai MP4 hoặc file input không cập nhật.");
    return {filename:value.name, bytes:value.size};
  } catch(error) {
    error.diagnostics=diagnostics;
    throw error;
  } finally {
    if (attached) await chromeApi.debugger.detach(target);
  }
}

if (typeof module !== "undefined") module.exports = {channelFromStudioUrl, fileInputs,
  inspectFileInputs, selectStudioFile};
