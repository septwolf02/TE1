const assert=require("node:assert/strict");
const {channelFromStudioUrl,fileInputs,selectStudioFile}=require("../studio_connector_extension/upload.js");
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
const event=()=>{let listener;return {addListener(fn){listener=fn},emit(value){listener?.(value)}}};

(async()=>{
  assert.equal(channelFromStudioUrl(url(A)),A);
  assert.equal(channelFromStudioUrl("https://evil.test/channel/"+A),"");
  assert.deepEqual(fileInputs(tree),[4]);
  assert.deepEqual(fileInputs({children:[{nodeId:3,nodeName:"INPUT",attributes:["type","file"]}]}),[]);
  const calls=[];
  let tabUrl=url(A), receipt={name:file.filename,size:file.bytes}, attached=false, attachFails=false;
  const chromeApi={tabs:{async get(){return {id:7,url:tabUrl}},
      async query(){return [{id:7,url:tabUrl}]},async update(){return {id:7,url:tabUrl}},
      async create(){return {id:7,url:tabUrl}},
      async sendMessage(){return {page_url:tabUrl,page_url_after:tabUrl}}},
    debugger:{async attach(){if(attachFails) throw new Error("debugger busy");
      attached=true;calls.push("attach")},
      async detach(){attached=false;calls.push("detach")},
      async sendCommand(target,method,params){calls.push(method);
        if(method==="DOM.getDocument") return {root:tree};
        if(method==="DOM.resolveNode") return {object:{objectId:"input"}};
        if(method==="Runtime.callFunctionOn") return {result:{value:receipt}};
        if(method==="DOM.setFileInputFiles") assert.deepEqual(params.files,[file.path]);
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
})().catch(error=>{console.error(error);process.exitCode=1});
