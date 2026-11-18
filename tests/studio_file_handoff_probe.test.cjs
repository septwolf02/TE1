const assert=require("node:assert/strict");
const {validProbeRequest,probeFileInput}=require("../studio_file_handoff_probe/background.js");

const request={type:"probe_file_input",path:"C:\\test\\sample.mp4",expectedSize:1024};

(async()=>{
  assert(validProbeRequest(request));
  assert(!validProbeRequest({...request,path:"C:\\test\\secret.txt"}));
  assert(!validProbeRequest({...request,path:"file:///C:/test/sample.mp4"}));
  assert(!validProbeRequest({...request,path:"C:\\test\\sample.mp4\n"}));
  assert(!validProbeRequest({...request,expectedSize:0}));
  const calls=[];
  let tabUrl="http://127.0.0.1:34567/", receipt={name:"sample.mp4",size:1024};
  const chromeApi={tabs:{async query(){return [{id:7,url:tabUrl}]}},
    debugger:{async attach(target,version){calls.push(["attach",target,version]);},
      async sendCommand(target,method,params){calls.push([method,params]);
        if(method==="DOM.getDocument") return {root:{nodeId:1}};
        if(method==="DOM.querySelector") return {nodeId:2};
        if(method==="Runtime.evaluate") return {result:{value:receipt}};
        return {};
      },async detach(target){calls.push(["detach",target]);}}};
  const result=await probeFileInput(chromeApi,request);
  assert.deepEqual(result,{received:true,filename:"sample.mp4",bytes:1024});
  assert(calls.some(call=>call[0]==="DOM.setFileInputFiles" &&
    call[1].files[0]===request.path && call[1].nodeId===2));
  assert.equal(calls.at(-1)[0],"detach");
  tabUrl="https://studio.youtube.com/channel/UC"+"a".repeat(22);
  await assert.rejects(probeFileInput(chromeApi,request),/Chỉ dùng trang thử nghiệm/);
  assert.equal(calls.at(-1)[0],"detach");
  tabUrl="http://127.0.0.1:34567/";
  receipt={name:"wrong.mp4",size:1024};
  await assert.rejects(probeFileInput(chromeApi,request),/không khớp/);
  assert.equal(calls.at(-1)[0],"detach");
})().catch(error=>{console.error(error);process.exitCode=1});
