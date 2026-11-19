"use strict";

const PRIVATE_NAMES=new Set(["private","riêng tư","非公開"]);
const NEXT_NAMES=new Set(["next","tiếp","次へ"]);
const SAVE_NAMES=new Set(["save","lưu","保存"]);
const NO_KIDS_NAMES=new Set(["no, it's not made for kids","không, video này không dành cho trẻ em",
  "いいえ、子ども向けではありません"]);
const KIDS_NAMES=new Set(["yes, it's made for kids","có, video này dành cho trẻ em",
  "はい、子ども向けです"]);
const PRIVATE_TEXT=new Set(["private","riêng tư","非公開"]);
const CHECKS_HEADINGS=new Set(["checks","kiểm tra","チェック"]);
const NO_ISSUES=new Set(["no issues found","không phát hiện vấn đề","問題は見つかりませんでした"]);
const VIDEO_ID_RE=/(?:youtube\.com\/watch\?v=|youtu\.be\/|(?:studio\.youtube\.com)?\/video\/)([A-Za-z0-9_-]{11})(?:[/?&#]|$)/;

function uploadDialog(root,axNames=new Map()) {
  const dialogs=walk(root).filter(({node})=>isUploadDialog(node,axNames));
  return dialogs.length===1?dialogs[0].node:null;
}

function nodeNames(node,axNames=new Map()) {
  const a=attrs(node);
  return [axNames.get(node.backendNodeId),a["aria-label"],a.placeholder,a.title,a.id,
    controlText(node)].filter(value=>typeof value==="string"&&value.trim())
    .map(normalizedText);
}

function uniqueControl(root,names,predicate,axNames=new Map()) {
  const found=walk(root).filter(({node,chain})=>node.nodeId && predicate(node,chain) &&
    !chain.some(item=>"hidden" in attrs(item)||attrs(item)["aria-hidden"]==="true") &&
    nodeNames(node,axNames).some(name=>names.has(name)));
  const innermost=found.filter(item=>!found.some(other=>other!==item&&other.chain.includes(item.node)));
  return innermost.length===1?innermost[0].node:null;
}

function textField(dialog,kind,axNames=new Map()) {
  const labels=kind==="title"?new Set(["title","title (required)","tiêu đề",
    "tiêu đề (bắt buộc)","タイトル","title-textarea"]):
    new Set(["description","mô tả","説明","description-textarea"]);
  const fields=walk(dialog).filter(({node})=>{
    const a=attrs(node),tag=node.nodeName?.toUpperCase();
    return node.nodeId && (tag==="INPUT"||tag==="TEXTAREA"||a.contenteditable==="true") &&
      nodeNames(node,axNames).some(name=>labels.has(name));
  });
  return fields.length===1?fields[0].node:null;
}

function thumbnailInput(dialog) {
  const found=walk(dialog).filter(({node})=>{
    const a=attrs(node);
    return node.nodeId && node.nodeName?.toUpperCase()==="INPUT"&&
      a.type?.toLowerCase()==="file"&&/image|\.png|\.jpe?g/i.test(a.accept||"")&&
      !/video/i.test(a.accept||"");
  });
  return found.length===1?found[0].node:null;
}

function blockingAlert(dialog) {
  if(walk(dialog).some(({node})=>{
    const a=attrs(node);
    return (a.role==="alert"||a.role==="alertdialog") &&
      !!controlText(node).trim() && !/no issues|không có vấn đề|問題はありません/i.test(controlText(node));
  })) return true;
  const nodes=walk(dialog);
  const checks=nodes.some(({node})=>{
    const a=attrs(node),tag=node.nodeName?.toUpperCase()||"";
    return (a.role==="heading"||/^H[1-6]$/.test(tag)) &&
      CHECKS_HEADINGS.has(normalizedText(controlText(node)));
  });
  return checks && !nodes.some(({node})=>{
    const a=attrs(node),tag=node.nodeName?.toUpperCase()||"";
    return (a.role==="status"||tag==="SPAN"||tag==="DIV") &&
      NO_ISSUES.has(normalizedText(controlText(node)));
  });
}

function privateRow(root,title) {
  const matches=new Set();
  for(const {node,chain} of walk(root)) {
    if(node.nodeName?.toUpperCase()!=="A") continue;
    const match=(attrs(node).href||"").match(VIDEO_ID_RE);
    if(!match) continue;
    const row=[...chain].reverse().find(item=>{
      const a=attrs(item),tag=item.nodeName?.toUpperCase()||"";
      return a.role==="row"||tag==="TR"||/(?:^|-)VIDEO(?:-|$).*ROW|(?:^|-)ROW$/.test(tag);
    });
    if(!row) continue;
    const labels=walk(row).filter(({node})=>node.nodeName==="#text")
      .map(({node})=>normalizedText(node.nodeValue));
    if(labels.some(label=>label===normalizedText(title)) &&
        labels.some(label=>PRIVATE_TEXT.has(label))) matches.add(match[1]);
  }
  return matches.size===1?[...matches][0]:"";
}

async function completePrivateUpload(chromeApi,tabId,task,native,options={}) {
  if(!task||!/^UC[A-Za-z0-9_-]{22}$/.test(task.channel_id||"")||
      !/^[a-f0-9]{32}$/.test(task.request_id||"")||
      !task.title?.trim()||(task.stage==="file_selected"&&!task.thumbnail_path))
    throw new Error("Tác vụ Studio không hợp lệ.");
  const target={tabId},pause=options.pause||((ms)=>new Promise(resolve=>setTimeout(resolve,ms)));
  const checkChannel=async()=>{
    const tab=await chromeApi.tabs.get(tabId);
    if(channelFromStudioUrl(tab.url)!==task.channel_id) {
      const error=new Error("Channel ID của Studio đã đổi; dừng thao tác upload.");
      error.code="channel_mismatch";
      throw error;
    }
    return tab.url;
  };
  let attached=false;
  try {
    await chromeApi.debugger.attach(target,"1.3");attached=true;
    const send=(method,params={})=>{
      let timer;
      return Promise.race([chromeApi.debugger.sendCommand(target,method,params),
        new Promise((_,reject)=>{timer=setTimeout(()=>reject(new Error(`CDP ${method} quá hạn 10 giây.`)),10000)})])
        .finally(()=>clearTimeout(timer));
    };
    const snapshot=async()=>{
      await checkChannel();
      const tree=await send("DOM.getDocument",{depth:-1,pierce:true});
      let names=new Map();
      try {const ax=await send("Accessibility.getFullAXTree");
        names=new Map((ax.nodes||[]).filter(item=>item.backendDOMNodeId&&item.name?.value)
          .map(item=>[item.backendDOMNodeId,item.name.value]));} catch (_) {}
      return {root:tree.root,dialog:uploadDialog(tree.root,names),
        dialogCount:walk(tree.root).filter(({node})=>isUploadDialog(node,names)).length,names};
    };
    const waitFor=async(predicate,ms=30000)=>{
      const deadline=Date.now()+ms;
      do {const state=await snapshot();if(predicate(state)) return state;
        if(Date.now()>=deadline) break;await pause(options.pollMs??500);} while(true);
      throw new Error("Giao diện Studio chưa hiển thị bước cần thiết; kiểm tra tab upload.");
    };
    const resolve=async node=>{
      const result=await send("DOM.resolveNode",{nodeId:node.nodeId});
      if(!result.object?.objectId) throw new Error("Control Studio không còn tồn tại.");
      return result.object.objectId;
    };
    const call=async(node,body,args=[])=>send("Runtime.callFunctionOn",{
      objectId:await resolve(node),functionDeclaration:body,
      arguments:args.map(value=>({value})),returnByValue:true});
    const click=async node=>{
      await checkChannel();
      const response=await call(node,"function(){if(!this.getClientRects().length||this.disabled)return false;this.click();return true}");
      if(response.result?.value!==true) throw new Error("Control Studio bị ẩn hoặc không thể bấm.");
    };
    if(task.stage==="file_selected") {
      await native("entering_details");
      const state=await waitFor(({dialog,names})=>dialog&&textField(dialog,"title",names)&&
        textField(dialog,"description",names));
      const fill=async(node,value)=>{
        const response=await call(node,"function(v){this.focus();if('value' in this){const p=this.tagName==='TEXTAREA'?HTMLTextAreaElement.prototype:HTMLInputElement.prototype;Object.getOwnPropertyDescriptor(p,'value').set.call(this,v)}else if(this.isContentEditable){this.textContent=v}else{return null}this.dispatchEvent(new InputEvent('input',{bubbles:true,inputType:'insertText',data:v}));this.dispatchEvent(new Event('change',{bubbles:true}));return 'value' in this?this.value:this.textContent}",[value]);
        if(response.result?.value!==value) throw new Error("Studio không giữ nội dung trường video.");
      };
      await fill(textField(state.dialog,"title",state.names),task.title);
      await fill(textField(state.dialog,"description",state.names),task.description||"");
      await pause(250);
      const confirmed=await snapshot();
      for(const [kind,wanted] of [["title",task.title],["description",task.description||""]]) {
        const field=confirmed.dialog&&textField(confirmed.dialog,kind,confirmed.names);
        if(!field) throw new Error(`Studio không còn trường ${kind} sau khi nhập.`);
        const value=await call(field,"function(){return 'value' in this?this.value:this.textContent}");
        if(value.result?.value!==wanted) throw new Error(`Studio không giữ nội dung ${kind}.`);
      }
      const thumb=thumbnailInput(confirmed.dialog);
      if(!thumb) throw new Error("Không xác định được ô thumbnail duy nhất trong dialog Studio.");
      await checkChannel();
      await send("DOM.setFileInputFiles",{nodeId:thumb.nodeId,files:[task.thumbnail_path]});
      const receipt=await call(thumb,"function(){const f=this.files&&this.files[0];return f?{name:f.name,size:f.size}:null}");
      if(receipt.result?.value?.name!==task.thumbnail_name||
          receipt.result?.value?.size!==task.thumbnail_bytes)
        throw new Error("Chrome chưa nhận đúng thumbnail đã chọn trong TE.");
      await native("thumbnail_selected");
      const wanted=task.audience==="made_for_kids"?KIDS_NAMES:NO_KIDS_NAMES;
      const radio=uniqueControl(confirmed.dialog,wanted,(node)=>{
        const a=attrs(node);return a.role==="radio"||node.nodeName?.toUpperCase()==="INPUT"&&a.type==="radio";
      },confirmed.names);
      if(!radio) throw new Error("Không xác định được lựa chọn đối tượng video trong Studio.");
      await click(radio);
      const checked=await call(radio,"function(){return this.checked===true||this.getAttribute('aria-checked')==='true'}");
      if(checked.result?.value!==true) throw new Error("Studio chưa xác nhận đối tượng video.");
      await native("audience_selected");
      // Tags are optional. Do not touch an unrelated field when Studio does not label one uniquely.
      for(let step=0;step<4;step++) {
        const page=await snapshot();
        if(!page.dialog) throw new Error("Hộp Studio đóng trước khi chọn Private.");
        if(blockingAlert(page.dialog)) throw new Error("Studio báo cảnh báo cần người dùng xem xét.");
        const privateChoice=uniqueControl(page.dialog,PRIVATE_NAMES,(node)=>{
          const a=attrs(node);return a.role==="radio"||node.nodeName?.toUpperCase()==="INPUT"&&a.type==="radio";
        },page.names);
        if(privateChoice) break;
        const next=uniqueControl(page.dialog,NEXT_NAMES,(node)=>{
          const a=attrs(node),tag=node.nodeName?.toUpperCase();
          return tag==="BUTTON"||tag?.endsWith("-BUTTON")||a.role==="button";
        },page.names);
        if(!next) throw new Error("Không thấy nút Tiếp duy nhất; cần kiểm tra Studio.");
        const before=controlText(page.dialog);
        await click(next);
        await waitFor(({dialog,names})=>dialog&&(
          uniqueControl(dialog,PRIVATE_NAMES,(node)=>attrs(node).role==="radio",names)||
          controlText(dialog)!==before),15000);
      }
      const visibility=await waitFor(({dialog,names})=>dialog&&
        uniqueControl(dialog,PRIVATE_NAMES,(node)=>attrs(node).role==="radio",names));
      await native("setting_private");
      if(blockingAlert(visibility.dialog)) throw new Error("Studio báo cảnh báo cần người dùng xem xét.");
      const privateChoice=uniqueControl(visibility.dialog,PRIVATE_NAMES,
        (node)=>attrs(node).role==="radio",visibility.names);
      await click(privateChoice);
      const privateChecked=await call(privateChoice,"function(){return this.checked===true||this.getAttribute('aria-checked')==='true'}");
      if(privateChecked.result?.value!==true) throw new Error("Studio chưa xác nhận chế độ Private.");
      const save=uniqueControl(visibility.dialog,SAVE_NAMES,(node)=>{
        const a=attrs(node),tag=node.nodeName?.toUpperCase();
        return tag==="BUTTON"||tag?.endsWith("-BUTTON")||a.role==="button";
      },visibility.names);
      if(!save) throw new Error("Không tìm thấy nút Lưu duy nhất ở bước Private.");
      await native("saving_private"); // The attempt is uncertain before the final click.
      await click(save);
    }
    await native("verifying_video");
    await waitFor(({dialogCount})=>dialogCount===0,options.saveWaitMs??60000);
    await checkChannel();
    await chromeApi.tabs.update(tabId,{url:`https://studio.youtube.com/channel/${task.channel_id}/videos`});
    const row=await waitFor(({root})=>privateRow(root,task.title),options.verifyWaitMs??60000);
    const videoId=privateRow(row.root,task.title);
    if(!videoId) throw new Error("Không thấy video Private trong danh sách của đúng kênh Studio.");
    const pageUrl=await checkChannel();
    return {video_id:videoId,page_url:pageUrl,visibility:"private",tags_applied:!task.tags?.trim()};
  } finally {if(attached) await chromeApi.debugger.detach(target);}
}

if(typeof module!=="undefined") module.exports={uploadDialog,textField,thumbnailInput,
  blockingAlert,privateRow,completePrivateUpload};
