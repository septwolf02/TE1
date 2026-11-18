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
  await vm.runInContext("checkUpload()",context);
  assert.deepEqual(sent,[]); // A live Studio tab is not treated as a disconnect on each alarm.
  tab=null;
  await vm.runInContext("checkUpload()",context);
  assert.deepEqual(sent,["upload_failed","upload_get"]);
  assert.equal(marker,undefined);
  marker=active;
  context.nativeRequest=async()=>{throw new Error("TE offline")};
  await vm.runInContext("checkUpload()",context);
  assert.equal(marker,active); // Retry reporting an uncertain outcome after TE returns.
})().catch(error=>{console.error(error);process.exitCode=1});
