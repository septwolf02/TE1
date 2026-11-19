const assert=require("node:assert/strict");
const fs=require("node:fs");
const vm=require("node:vm");
const path=require("node:path");

const channel="UC"+"a".repeat(22);
const url=`https://studio.youtube.com/channel/${channel}/videos/upload`;
const active={request_id:"a".repeat(32),channel_id:channel,tab_id:7};
let tab={id:7,url}, marker=active;
const sent=[];
const chrome={storage:{local:{async get(){return {studioActiveUpload:marker}},
  async set(value){marker=value.studioActiveUpload},async remove(){marker=undefined}}},
  tabs:{async get(){if(!tab) throw new Error("closed");return tab},onRemoved:{addListener(){}}},
  alarms:{create(){},onAlarm:{addListener(){}}},
  runtime:{onInstalled:{addListener(){}},onStartup:{addListener(){}}},
  sidePanel:{setPanelBehavior(){}}};
const context=vm.createContext({chrome,importScripts(){},
  channelFromStudioUrl(value){return value.match(/\/channel\/(UC[A-Za-z0-9_-]{22})/)?.[1]||""},
  async nativeRequest(_chrome,message){sent.push(message.type);return {ok:true,request:{pending:false}}},
  async processUploadRequest(){throw new Error("not expected")}});
vm.runInContext(fs.readFileSync(path.join(__dirname,"../studio_connector_extension/background.js"),"utf8"),context);
(async()=>{
  await new Promise(resolve=>setImmediate(resolve)); // Initial worker evaluation polls automatically.
  assert(sent.includes("upload_state"));
  sent.length=0;
  await vm.runInContext("checkUpload()",context);
  assert.deepEqual(sent,["upload_state"]); // A live tab is not treated as a disconnect.
  tab=null;
  await vm.runInContext("checkUpload()",context);
  assert.deepEqual(sent,["upload_state","upload_state","upload_failed","upload_get"]);
  assert.equal(marker,undefined);
  marker=active;
  context.nativeRequest=async()=>{throw new Error("TE offline")};
  await vm.runInContext("checkUpload()",context);
  assert.equal(marker,active); // Retry reporting an uncertain outcome after TE returns.
  const next={pending:true,request_id:"b".repeat(32),channel_id:channel};
  let processed=0;
  context.nativeRequest=async(_chrome,message)=>message.type==="upload_state"?
    {ok:true,safe_to_clear:true}:{ok:true,request:next};
  context.processUploadRequest=async()=>{processed++;return {tabId:8}};
  await vm.runInContext("checkUpload()",context);
  assert.equal(processed,1);
  assert.equal(marker.request_id,next.request_id);
  assert.equal(marker.tab_id,8); // New request runs without opening the Side Panel.
  let freshMarker, freshProcessed=0, alarmCreated=0;
  const freshChrome={storage:{local:{async get(){return {studioActiveUpload:freshMarker}},
    async set(value){freshMarker=value.studioActiveUpload},async remove(){freshMarker=undefined}}},
    tabs:{onRemoved:{addListener(){}}},alarms:{create(){alarmCreated++},
      onAlarm:{addListener(){}}},runtime:{onInstalled:{addListener(){}},
      onStartup:{addListener(){}}},sidePanel:{setPanelBehavior(){}}};
  const fresh=vm.createContext({chrome:freshChrome,importScripts(){},
    async nativeRequest(_chrome,message){assert.equal(message.type,"upload_get");
      return {ok:true,request:next}},
    async processUploadRequest(){freshProcessed++;return {tabId:4}},
    channelFromStudioUrl(){return channel}});
  vm.runInContext(fs.readFileSync(path.join(__dirname,
    "../studio_connector_extension/background.js"),"utf8"),fresh);
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(alarmCreated,1);
  assert.equal(freshProcessed,1);
  assert.equal(freshMarker.request_id,next.request_id);
})().catch(error=>{console.error(error);process.exitCode=1});
