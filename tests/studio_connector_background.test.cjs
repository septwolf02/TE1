const assert=require("node:assert/strict");
const fs=require("node:fs");
const vm=require("node:vm");
const path=require("node:path");
const channel="UC"+"a".repeat(22);
const url=`https://studio.youtube.com/channel/${channel}/videos/upload`;
const requestId="a".repeat(32);
const source=fs.readFileSync(path.join(__dirname,"../studio_connector_extension/background.js"),"utf8");
const tick=()=>new Promise(resolve=>setImmediate(resolve));
function worker(initialMarker,initialTab) {
  let marker=initialMarker,tab=initialTab,alarmCreated=0;
  const sent=[];
  const chrome={storage:{local:{async get(){return {studioActiveUpload:marker}},
    async set(value){marker=value.studioActiveUpload},async remove(){marker=undefined}}},
    tabs:{async get(){if(!tab) throw new Error("closed");return tab},onRemoved:{addListener(){}}},
    alarms:{create(){alarmCreated++},onAlarm:{addListener(){}}},
    runtime:{onInstalled:{addListener(){}},onStartup:{addListener(){}}},
    sidePanel:{setPanelBehavior(){}}};
  const context=vm.createContext({chrome,importScripts(){},
    channelFromStudioUrl(value){return value.match(/\/channel\/(UC[A-Za-z0-9_-]{22})/)?.[1]||""},
    async nativeRequest(_chrome,message){sent.push(message.type);
      if(message.type==="upload_state") return {ok:true,safe_to_clear:false};
      if(message.type==="upload_resume") return {ok:true,task:{stage:"file_selected"}};
      return {ok:true,request:{pending:false}}},
    async completePrivateUpload(_chrome,id,task,progress){
      assert.equal(id,7);assert.equal(task.stage,"file_selected");
      await progress("saving_private");
      return {video_id:"a".repeat(11),page_url:url,visibility:"private"}},
    async processUploadRequest(){throw new Error("unexpected")}});
  vm.runInContext(source,context);
  return {context,sent,get marker(){return marker},set marker(value){marker=value},
    set tab(value){tab=value},get alarmCreated(){return alarmCreated}};
}
(async()=>{
  const live=worker({request_id:requestId,channel_id:channel,tab_id:7},{id:7,url});
  await tick();
  assert.deepEqual(live.sent,["upload_state","upload_resume","upload_progress","upload_complete"]);
  assert.equal(live.marker,undefined);
  assert.equal(live.alarmCreated,1);
  const closed=worker({request_id:requestId,channel_id:channel,tab_id:7},null);
  await tick();
  assert.deepEqual(closed.sent,["upload_state","upload_failed","upload_get"]);
  assert.equal(closed.marker,undefined);
  const offline=worker({request_id:requestId,channel_id:channel,tab_id:7},{id:7,url});
  offline.context.nativeRequest=async()=>{throw new Error("TE offline")};
  await tick();
  assert.equal(offline.marker.request_id,requestId);
  let processed=0;
  const fresh=worker(undefined,{id:7,url});
  fresh.context.nativeRequest=async(_chrome,message)=>{
    fresh.sent.push(message.type);
    if(message.type==="upload_get") return {ok:true,request:{pending:true,request_id:requestId,channel_id:channel}};
    if(message.type==="upload_resume") return {ok:true,task:{stage:"file_selected"}};
    return {ok:true};
  };
  fresh.context.processUploadRequest=async()=>{processed++;return {tabId:7}};
  await tick();
  assert.equal(processed,1);
  assert(fresh.sent.includes("upload_complete"));
  assert.equal(fresh.marker,undefined);
})().catch(error=>{console.error(error);process.exitCode=1});
