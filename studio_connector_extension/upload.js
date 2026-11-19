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

const UPLOAD_HEADINGS=new Set(["tải video lên","upload videos","upload video",
  "動画をアップロード","動画のアップロード"]);

function normalizedText(value) {
  return String(value||"").normalize("NFKC").replace(/\s+/g," ").trim().toLocaleLowerCase();
}

function isUploadDialog(node,axNames=new Map()) {
  if(!isDialog(node)) return false;
  const a=attrs(node);
  if("hidden" in a || a["aria-hidden"]==="true" ||
      /display\s*:\s*none|visibility\s*:\s*hidden/i.test(a.style||"")) return false;
  const identity=(node.nodeName||"")+" "+(a["aria-label"]||a.title||"")+
    " "+(axNames.get(node.backendNodeId)||"");
  if(/upload|アップロード|tải video/i.test(identity)) return true;
  const labelledBy=(a["aria-labelledby"]||"").split(/\s+/);
  return walk(node).slice(1).some(({node:child,chain})=>{
    if(chain.slice(1,-1).some(isDialog)) return false;
    const info=attrs(child),tag=child.nodeName?.toUpperCase()||"";
    const heading=/^H[1-6]$/.test(tag) || info.role==="heading" ||
      /(?:title|heading)/i.test(`${info.id||""} ${info.class||""}`) ||
      labelledBy.includes(info.id) && !!info.id;
    if(heading && UPLOAD_HEADINGS.has(normalizedText(controlText(child)))) return true;
    return child.nodeName==="#text" && UPLOAD_HEADINGS.has(normalizedText(child.nodeValue)) &&
      !chain.some(item=>{
        const role=attrs(item).role,tagName=item.nodeName?.toUpperCase()||"";
        return role==="button" || role==="menuitem" || tagName==="BUTTON" ||
          tagName.endsWith("-BUTTON");
      });
  });
}

function inspectFileInputs(root,axNames=new Map()) {
  const candidates=[],rejected=[];
  for(const {node,chain} of walk(root)) {
    if(node.nodeName?.toUpperCase()==="IFRAME" && !node.contentDocument)
      rejected.push("inaccessible_iframe");
    const a=attrs(node);
    if(node.nodeName?.toUpperCase()!=="INPUT" || a.type?.toLowerCase()!=="file") continue;
    const dialog=chain.some(item=>isUploadDialog(item,axNames)), accept=(a.accept||"").toLowerCase();
    const video=/video|\.mp4|\.mov|\.mkv|\.webm/.test(accept);
    const hidden=chain.some(item=>"hidden" in attrs(item)||attrs(item)["aria-hidden"]==="true");
    const reason=/image|audio/.test(accept)&&!video?"non_video_accept":
      !dialog?"outside_upload_dialog":"";
    if(reason) rejected.push(reason);
    candidates.push({nodeId:node.nodeId,reason,video,hidden});
  }
  return {candidates,eligible:candidates.filter(item=>!item.reason),
    rejected:[...new Set(rejected)],dialogOpen:walk(root).some(({node})=>isUploadDialog(node,axNames))};
}

function fileInputs(node) {return inspectFileInputs(node).eligible.map(item=>item.nodeId);}

function studioReaction(root, filename, axNames=new Map()) {
  let filenameShown=false, progressCount=0;
  for(const {node:dialog} of walk(root).filter(({node})=>isUploadDialog(node,axNames))) {
    for(const {node,chain} of walk(dialog)) {
      const a=attrs(node);
      if(node.nodeName==="#text" && node.nodeValue?.includes(filename) &&
          !chain.some(item=>item.nodeName?.toUpperCase()==="INPUT")) filenameShown=true;
      if(node.nodeName?.toUpperCase()==="PROGRESS" || a.role==="progressbar") progressCount++;
    }
  }
  return {filenameShown,progressCount};
}

function controlText(node) {
  const pieces=[];
  for(const {node:child} of walk(node)) {
    if(child.nodeName==="#text" && child.nodeValue) pieces.push(child.nodeValue);
  }
  return pieces.join(" ").replace(/\s+/g," ").trim();
}

function controlNames(node,axNames) {
  const a=attrs(node);
  return [axNames.get(node.backendNodeId),a["aria-label"],a.title,controlText(node)]
    .filter(value=>typeof value==="string" && value.trim())
    .map(value=>value.normalize("NFKC").replace(/\s+/g," ").trim().toLocaleLowerCase());
}

function uploadAction(root,clicked,axNames=new Map(),axControls=axNames) {
  const create=new Set(["create","作成","tạo"]);
  const upload=new Set(["upload videos","upload video","動画をアップロード",
    "動画のアップロード","tải video lên"]);
  const candidates=walk(root).filter(({node,chain})=>{
    const a=attrs(node),tag=node.nodeName?.toUpperCase()||"";
    return !!node.nodeId && (tag==="BUTTON" || tag.endsWith("-BUTTON") ||
      a.role==="button" || a.role==="menuitem" || axControls.has(node.backendNodeId)) && !a.disabled &&
      !chain.some(item=>"hidden" in attrs(item)||attrs(item)["aria-hidden"]==="true");
  }).map(item=>({...item,names:controlNames(item.node,axNames)}));
  const choose=(names,requireMenu,kind)=>{
    const matches=candidates.filter(item=>item.names.some(name=>names.has(name)) &&
      (!requireMenu || item.chain.some(node=>{
        const a=attrs(node);
        return a.role==="menu" || a.role==="menuitem" ||
          /(?:^|-)MENU(?:-|$)/.test(node.nodeName?.toUpperCase()||"");
      })));
    // A custom button may wrap one native button. Click the innermost match.
    const independent=matches.filter(item=>!matches.some(other=>other!==item &&
      other.chain.includes(item.node)));
    return independent.length===1 ? {nodeId:independent[0].node.nodeId,kind} : null;
  };
  if(clicked.has("create")) return choose(upload,false,"upload");
  return choose(create,false,"create") || choose(upload,false,"upload");
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
    let nodeId, beforeReaction, dialogAxNames=new Map();
    do {
      diagnostics.browser_step=clicked.has("create")?"waiting_for_create_menu":
        "waiting_for_upload_dialog";
      const tree=await send("DOM.getDocument",{depth:-1,pierce:true});
      let axNames=new Map(),axControls=new Map();
      try {
        const accessibility=await send("Accessibility.getFullAXTree");
        axNames=new Map((accessibility.nodes||[]).filter(item=>!item.ignored &&
          item.backendDOMNodeId && typeof item.name?.value==="string")
          .map(item=>[item.backendDOMNodeId,item.name.value]));
        axControls=new Map((accessibility.nodes||[]).filter(item=>!item.ignored &&
          item.backendDOMNodeId && ["button","menuitem"].includes(item.role?.value))
          .map(item=>[item.backendDOMNodeId,true]));
      } catch (_) { /* DOM heading and attributes remain available. */ }
      const found=inspectFileInputs(tree.root,axNames);
      diagnostics.dialog_open=found.dialogOpen;
      diagnostics.candidate_count=found.candidates.length;
      diagnostics.rejected=found.rejected;
      const strongest=found.eligible.filter(item=>item.video);
      const eligible=strongest.length===1 ? strongest : found.eligible;
      if(eligible.length===1) {
        nodeId=eligible[0].nodeId;
        dialogAxNames=axNames;
        beforeReaction=studioReaction(tree.root,file.filename,dialogAxNames);
        break;
      }
      if(found.eligible.length>1) diagnostics.rejected=[...found.rejected,"ambiguous_inputs"];
      if(!found.dialogOpen) {
        const action=uploadAction(tree.root,clicked,axNames,axControls);
        if(action) {
          const beforeClick=await chromeApi.tabs.get(tabId);
          if(channelFromStudioUrl(beforeClick.url)!==file.channel_id)
            throw new Error("Channel ID đã đổi trước khi mở menu upload.");
          const resolved=await send("DOM.resolveNode",{nodeId:action.nodeId});
          const result=await send("Runtime.callFunctionOn",{objectId:resolved.object.objectId,
            functionDeclaration:"function(){const s=getComputedStyle(this);if(s.display==='none'||s.visibility==='hidden'||!this.getClientRects().length)return false;this.click();return true}",
            returnByValue:true});
          if(result?.result?.value===true) {
            clicked.add(action.kind);
            diagnostics.browser_step=action.kind==="create"?"opening_create_menu":
              "opening_upload_dialog";
          } else {
            diagnostics.rejected=[...diagnostics.rejected,"control_not_visible"];
            break;
          }
        }
      }
      if(Date.now()>=deadline) break;
      await pause(options.pollMs??500);
    } while(true);
    if(!nodeId) {
      if(clicked.size && !diagnostics.dialog_open)
        diagnostics.browser_step="upload_dialog_action_sent_but_not_open";
      diagnostics.rejected=[...new Set([...diagnostics.rejected,
        diagnostics.dialog_open?"upload_input_missing":clicked.has("upload")?
          "upload_dialog_missing":clicked.has("create")?
          "upload_menu_item_missing":"create_control_missing"])].slice(0,10);
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
    diagnostics.browser_step="waiting_for_studio_reaction";
    const reactionDeadline=Date.now()+(options.reactionMs??30000);
    let reacted=false;
    do {
      const latest=await send("DOM.getDocument",{depth:-1,pierce:true});
      const reaction=studioReaction(latest.root,file.filename,dialogAxNames);
      if(reaction.filenameShown&&!beforeReaction.filenameShown ||
          reaction.progressCount>beforeReaction.progressCount) {reacted=true;break;}
      if(Date.now()>=reactionDeadline) break;
      await pause(options.pollMs??500);
    } while(true);
    if(!reacted) {
      diagnostics.rejected=[...new Set([...diagnostics.rejected,"studio_reaction_unconfirmed"])];
      const error=new Error("Chrome đã nhận MP4 nhưng chưa thấy YouTube Studio hiển thị tên tệp hoặc tiến trình. Kiểm tra Studio trước khi thử lại.");
      error.code="studio_no_reaction";throw error;
    }
    return {filename:value.name, bytes:value.size};
  } catch(error) {
    error.diagnostics=diagnostics;
    throw error;
  } finally {
    if (attached) await chromeApi.debugger.detach(target);
  }
}

if (typeof module !== "undefined") module.exports = {channelFromStudioUrl, fileInputs,
  inspectFileInputs, studioReaction, uploadAction, selectStudioFile,
  attrs, walk, isUploadDialog, normalizedText, controlText};
