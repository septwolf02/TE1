const assert=require("node:assert/strict");
const {channelFromStudioUrl,fileInputs,inspectFileInputs,studioReaction,uploadAction,selectStudioFile}=require("../studio_connector_extension/upload.js");
Object.assign(globalThis,{channelFromStudioUrl,fileInputs,selectStudioFile});
const {processUploadRequest}=require("../studio_connector_extension/sidepanel.js");

const A="UC"+"a".repeat(22), B="UC"+"b".repeat(22);
const url=id=>`https://studio.youtube.com/channel/${id}/videos/upload`;
const request={pending:true,request_id:"a".repeat(32),channel_id:A};
const file={request_id:request.request_id,channel_id:A,path:"C:\\TE\\video.mp4",
  filename:"video.mp4",bytes:283516921};
const tree={nodeId:1,nodeName:"#document",children:[{nodeId:2,nodeName:"YTCP-UPLOADS-DIALOG",
  shadowRoots:[{nodeId:3,nodeName:"#shadow-root",children:[{nodeId:4,nodeName:"INPUT",
    attributes:["type","file","accept","video/mp4"]}]}]}]};
const reactedTree=structuredClone(tree);
reactedTree.children[0].shadowRoots[0].children.push({nodeName:"#text",nodeValue:file.filename});
const event=()=>{let listener;return {addListener(fn){listener=fn},emit(value){listener?.(value)}}};

(async()=>{
  assert.equal(studioReaction(tree,file.filename).filenameShown,false);
  assert.equal(studioReaction(reactedTree,file.filename).filenameShown,true);
  const progressTree=structuredClone(tree);
  progressTree.children[0].shadowRoots[0].children.push({nodeName:"DIV",
    attributes:["role","progressbar"]});
  assert.equal(studioReaction(progressTree,file.filename).progressCount,1);
  assert.equal(channelFromStudioUrl(url(A)),A);
  assert.equal(channelFromStudioUrl("https://evil.test/channel/"+A),"");
  assert.deepEqual(fileInputs(tree),[4]);
  assert.deepEqual(fileInputs({children:[{nodeId:3,nodeName:"INPUT",attributes:["type","file"]}]}),[]);
  const emptyAccept=structuredClone(tree);
  emptyAccept.children[0].shadowRoots[0].children[0].attributes=["type","file"];
  assert.deepEqual(fileInputs(emptyAccept),[4]);
  const headedDialog={nodeId:30,nodeName:"YTCP-DIALOG",children:[
    {nodeId:31,nodeName:"DIV",attributes:["id","dialog-title"],
      children:[{nodeName:"#text",nodeValue:"Tải video lên"}]},
    {nodeId:32,nodeName:"INPUT",attributes:["type","file","hidden",""]},
    {nodeId:33,nodeName:"INPUT",attributes:["type","file","accept","image/png","hidden",""]}]};
  const headed=inspectFileInputs(headedDialog);
  assert.equal(headed.dialogOpen,true);
  assert.deepEqual(headed.eligible.map(item=>item.nodeId),[32]);
  assert.equal(headed.eligible[0].hidden,true);
  assert(headed.rejected.includes("non_video_accept"));
  const plainHeading=structuredClone(headedDialog);
  plainHeading.children[0].attributes=[];
  assert.equal(inspectFileInputs(plainHeading).dialogOpen,true);
  const noHeading=structuredClone(headedDialog);
  noHeading.children[0].children[0].nodeValue="Đổi hình thu nhỏ";
  assert.deepEqual(fileInputs(noHeading),[]);
  const inactive=structuredClone(headedDialog);
  inactive.attributes=["style","display:none"];
  assert.deepEqual(fileInputs(inactive),[]);
  const unrelatedDialog=structuredClone(emptyAccept);
  unrelatedDialog.children[0].nodeName="YTCP-THUMBNAIL-DIALOG";
  assert.deepEqual(fileInputs(unrelatedDialog),[]);
  assert.deepEqual(fileInputs({nodeName:"IFRAME",contentDocument:emptyAccept}),[4]);
  assert(inspectFileInputs({nodeName:"IFRAME"}).rejected.includes("inaccessible_iframe"));
  const axOnly={nodeName:"#document",children:[{nodeId:21,backendNodeId:121,nodeName:"YTCP-VE"}]};
  assert.equal(uploadAction(axOnly,new Set(),new Map([[121,"Tạo"]])).kind,"create");
  assert.equal(uploadAction({nodeName:"#document",children:[
    {nodeId:21,nodeName:"BUTTON",children:[{nodeName:"#text",nodeValue:"Tạo"}]},
    {nodeId:22,nodeName:"BUTTON",children:[{nodeName:"#text",nodeValue:"Tạo"}]}]},
    new Set()),null);
  const imageOnly=structuredClone(tree);
  imageOnly.children[0].shadowRoots[0].children[0].attributes=["type","file","accept","image/png"];
  assert.equal(inspectFileInputs(imageOnly).rejected[0],"non_video_accept");
  const calls=[];
  let tabUrl=url(A), receipt={name:file.filename,size:file.bytes}, attached=false, attachFails=false,
      fileWasSet=false;
  const chromeApi={tabs:{async get(){return {id:7,url:tabUrl}},
      async query(){return [{id:7,url:tabUrl}]},async update(){return {id:7,url:tabUrl}},
      async create(){return {id:7,url:tabUrl}},
      async sendMessage(){return {page_url:tabUrl,page_url_after:tabUrl}}},
    debugger:{async attach(){if(attachFails) throw new Error("debugger busy");
      attached=true;fileWasSet=false;calls.push("attach")},
      async detach(){attached=false;calls.push("detach")},
      async sendCommand(target,method,params){calls.push(method);
        if(method==="DOM.getDocument") return {root:fileWasSet?reactedTree:tree};
        if(method==="DOM.resolveNode") return {object:{objectId:"input"}};
        if(method==="Runtime.callFunctionOn") return {result:{value:receipt}};
        if(method==="DOM.setFileInputFiles") {assert.deepEqual(params.files,[file.path]);fileWasSet=true;}
        return {};
      }}};
  let started=0;
  assert.deepEqual(await selectStudioFile(chromeApi,7,file,async()=>{started++}),
    {filename:file.filename,bytes:file.bytes});
  assert.equal(started,1);assert(!attached);assert.equal(calls.at(-1),"detach");
  tabUrl=url(B);
  await assert.rejects(selectStudioFile(chromeApi,7,file,async()=>{started++}),/Channel ID/);
  assert.equal(started,1);assert.equal(calls.at(-1),"detach");
  tabUrl=url(A);receipt={name:"wrong.mp4",size:file.bytes};
  await assert.rejects(selectStudioFile(chromeApi,7,file,async()=>{started++}),/sai MP4/);
  assert.equal(calls.at(-1),"detach");
  receipt={name:file.filename,size:file.bytes};

  const requests=[], statuses=[];
  chromeApi.runtime={connectNative(){const onMessage=event(),onDisconnect=event();
    return {onMessage,onDisconnect,disconnect(){},postMessage(payload){
      requests.push(payload);
      const reply=payload.type==="upload_claim"?{ok:true,file}: {ok:true};
      setTimeout(()=>onMessage.emit(reply),0);
    }};
  }};
  await processUploadRequest(chromeApi,request,message=>statuses.push(message),{hostTimeout:1000});
  assert.deepEqual(requests.map(item=>item.type),
    ["upload_claim","upload_started","upload_selected"]);
  assert(statuses.at(-1).includes("chưa xác nhận"));
  assert(!attached);
  requests.length=0;tabUrl=url(B);
  await assert.rejects(processUploadRequest(chromeApi,request,()=>{},{hostTimeout:1000}),/Channel ID/);
  assert.deepEqual(requests.map(item=>item.type),["upload_failed"]);
  assert.equal(requests[0].reason,"channel_mismatch");
  requests.length=0;tabUrl=url(A);attachFails=true;
  await assert.rejects(processUploadRequest(chromeApi,request,()=>{},{hostTimeout:1000}),/debugger busy/);
  assert.deepEqual(requests.map(item=>item.type),["upload_claim","upload_failed"]);
  assert.equal(requests.at(-1).reason,"debugger_failure");
  attachFails=false;
  let reads=0;
  const original=chromeApi.debugger.sendCommand;
  chromeApi.debugger.sendCommand=async(target,method,params)=>{
    if(method==="DOM.getDocument") return {root:++reads<3?{nodeId:1,nodeName:"#document"}:
      fileWasSet?reactedTree:emptyAccept};
    return original(target,method,params);
  };
  await selectStudioFile(chromeApi,7,file,async()=>{},
    {waitMs:1000,pollMs:0,pause:async()=>{}});
  assert.equal(reads,4); // A dialog that appears later is accepted, then Studio reacts.
  chromeApi.debugger.sendCommand=async(target,method,params)=>
    method==="DOM.getDocument"?{root:{nodeId:1,nodeName:"#document"}}:original(target,method,params);
  await assert.rejects(selectStudioFile(chromeApi,7,file,async()=>{},
    {waitMs:0,pause:async()=>{}}),error=>error.code==="missing_input" &&
      error.diagnostics.candidate_count===0 && error.diagnostics.cdp_attached);
  const multiple=structuredClone(tree);
  multiple.children[0].shadowRoots[0].children.push({nodeId:5,nodeName:"INPUT",
    attributes:["type","file","accept","video/mp4"]});
  chromeApi.debugger.sendCommand=async(target,method,params)=>
    method==="DOM.getDocument"?{root:multiple}:original(target,method,params);
  await assert.rejects(selectStudioFile(chromeApi,7,file,async()=>{},
    {waitMs:0,pause:async()=>{}}),error=>error.code==="missing_input" &&
      error.diagnostics.rejected.includes("ambiguous_inputs"));
  let opened=false, clicks=0;
  const openButton={nodeId:9,nodeName:"BUTTON",attributes:["aria-label","Upload videos"]};
  chromeApi.debugger.sendCommand=async(target,method,params)=>{
    if(method==="DOM.getDocument") return {root:opened?(fileWasSet?reactedTree:emptyAccept):
      {nodeId:1,nodeName:"#document",children:[openButton]}};
    if(method==="Runtime.callFunctionOn" && params.functionDeclaration.includes("this.click()")) {
      opened=true;clicks++;return {result:{value:true}};
    }
    return original(target,method,params);
  };
  await selectStudioFile(chromeApi,7,file,async()=>{},
    {waitMs:1000,pause:async()=>{}});
  assert.equal(clicks,1); // An unambiguous accessible upload action opens the dialog once.

  let stage="create", createClicks=0, uploadClicks=0;
  const createButton={nodeId:11,backendNodeId:111,nodeName:"YTCP-BUTTON",
    children:[{nodeName:"#text",nodeValue:"Tạo"}]};
  const uploadMenu={nodeId:12,nodeName:"DIV",attributes:["role","menu"],
    children:[{nodeId:13,backendNodeId:113,nodeName:"YTCP-VE",attributes:["role","menuitem"],
      children:[{nodeName:"#text",nodeValue:"Tải video lên"}]}]};
  chromeApi.debugger.sendCommand=async(target,method,params)=>{
    if(method==="DOM.getDocument") return {root:stage==="create"?
      {nodeId:1,nodeName:"#document",children:[createButton]}:stage==="menu"?
      {nodeId:1,nodeName:"#document",children:[uploadMenu]}:fileWasSet?reactedTree:emptyAccept};
    if(method==="Runtime.callFunctionOn" && params.functionDeclaration.includes("this.click()")) {
      if(stage==="create") {stage="menu";createClicks++;}
      else {stage="dialog";uploadClicks++;}
      return {result:{value:true}};
    }
    return original(target,method,params);
  };
  await selectStudioFile(chromeApi,7,file,async()=>{},
    {waitMs:1000,pause:async()=>{}});
  assert.equal(createClicks,1);assert.equal(uploadClicks,1);
  // The Create label came from a custom element's nested visible text.
  chromeApi.debugger.sendCommand=async(target,method,params)=>
    method==="DOM.getDocument"?{root:tree}:original(target,method,params);
  let uncertain=0;
  await assert.rejects(selectStudioFile(chromeApi,7,file,async()=>{uncertain++},
    {reactionMs:0,pause:async()=>{}}),error=>error.code==="studio_no_reaction" &&
      error.diagnostics.browser_step==="waiting_for_studio_reaction");
  assert.equal(uncertain,1); // A populated input without a Studio UI reaction is uncertain.
})().catch(error=>{console.error(error);process.exitCode=1});
