const assert = require("node:assert/strict");
const {mergeCatalog, editName, markDuplicates} = require("../studio_connector_extension/catalog.js");
Object.assign(globalThis,{mergeCatalog,editName,markDuplicates});
const {startBulkScan, stopBulkScan, diagnoseChannelMenu} = require("../studio_connector_extension/content.js");
const {createPanel, withTimeout} = require("../studio_connector_extension/sidepanel.js");

const A = "UC" + "a".repeat(22), B = "UC" + "b".repeat(22);
const url = id => `https://studio.youtube.com/channel/${id}/videos`;
const observation = (id,name,handle="") => ({id,name,handle,source_url:url(id),
  id_source:"studio_link",name_source:"studio_switcher_dom",handle_source:handle?"studio_switcher_dom":"missing",
  evidence:"visible_switcher_item"});

function event() { let callback; return {addListener(fn){callback=fn;},emit(value,sender){callback?.(value,sender);}}; }
function fakeElement(tag="div") {
  return {tagName:tag.toUpperCase(), textContent:"", value:"", children:[], listeners:{},
    setAttribute(){}, addEventListener(name,callback){this.listeners[name]=callback;},
    fire(name){this.listeners[name]?.();}, append(child){this.children.push(child);},
    insertCell(){const cell=fakeElement("td");this.children.push(cell);return cell;},
    replaceChildren(){this.children=[];}};
}

(async () => {
  await assert.rejects(withTimeout(new Promise(()=>{}), 10, "Quá hạn"), /Quá hạn/);
  let rows = mergeCatalog([], [observation(A,"Same"),observation(B,"Same")], ()=>"one");
  assert.equal(rows.length,2);
  assert(rows.every(row=>row.duplicate_name));
  rows = editName(rows,"id:"+A,"Edited A");
  rows = mergeCatalog(rows,[observation(A,"New DOM"),observation(B,"Same")]);
  assert.equal(rows.find(row=>row.id===A).display_name,"Edited A");
  assert.equal(rows.find(row=>row.id===A).discovered_name,"New DOM");
  const unresolved = {id:"",name:"Candidate",handle:"@candidate",source_url:"https://studio.youtube.com/"};
  rows = mergeCatalog(rows,[unresolved]);
  rows = mergeCatalog(rows,[unresolved]);
  assert.equal(rows.filter(row=>!row.id).length,1);
  rows = mergeCatalog(rows,[{...unresolved,name:"Other",handle:""}]);
  assert.equal(rows.filter(row=>!row.id).length,2);
  const noHandle={...unresolved,handle:""};
  rows=mergeCatalog(rows,[noHandle,noHandle],()=>`new${Math.random()}`);
  const candidateCount=rows.filter(row=>!row.id).length;
  rows=mergeCatalog(rows,[noHandle,noHandle],()=>`new${Math.random()}`);
  assert.equal(rows.filter(row=>!row.id).length,candidateCount);
  assert.equal(rows.find(row=>row.id===A).display_name,"Edited A");

  const row={tagName:"YTCP-ACCOUNT-ITEM",id:"",className:"account-row",textContent:"secret@example.com",
    getClientRects:()=>[1],getAttribute:key=>key==="aria-label"?"private@example.com":null,
    querySelectorAll:()=>[],children:[],clientHeight:40,scrollHeight:40,scrollTop:0};
  const diagnosticMenu={tagName:"YTCP-ACCOUNT-SWITCHER",id:"account-menu",className:"switcher",
    getClientRects:()=>[1],getAttribute:key=>key==="role"?"menu":null,
    querySelectorAll:()=>[row],children:[row],clientHeight:100,scrollHeight:300,scrollTop:0};
  const diagnosticDoc={location:{href:url(A)},querySelectorAll:()=>[diagnosticMenu,row]};
  const diagnostic=diagnoseChannelMenu(diagnosticDoc);
  assert(diagnostic.candidates.some(item=>item.element.tag==="ytcp-account-switcher"));
  assert(diagnostic.candidates.some(item=>item.visible_row_candidates===1));
  assert(!JSON.stringify(diagnostic).includes("secret@example.com"));
  assert(!JSON.stringify(diagnostic).includes("private@example.com"));

  const events=[];
  let items=[], open=false;
  const entry=(name,id)=>({textContent:name,getClientRects:()=>[1],getAttribute:()=>null,querySelector:()=>null,
    closest:()=>({href:url(id)})});
  const menu={getClientRects:()=>[1],getAttribute:()=>null,querySelectorAll:()=>items};
  const doc={querySelectorAll:selector=>selector.includes("tp-yt-iron-dropdown") && selector.includes("ytcp-app")?[]:
    selector.includes("ytcp-account-switcher")&&open?[menu]:[],
    addEventListener(){},removeEventListener(){}};
  class Observer { constructor(callback){this.callback=callback;Observer.last=this;} observe(){} disconnect(){} }
  const closed=startBulkScan(doc,()=>url(A),update=>events.push(update),1000,Observer);
  assert.equal(closed.channels.length,1); // Active URL still works with switcher closed.
  stopBulkScan();
  open=true;
  items=[entry("Channel A",A)];
  const session=startBulkScan(doc,()=>url(A),update=>events.push(update),1000,Observer);
  assert(session.channels.some(row=>row.id===A));
  items=[entry("Channel B",B)]; // Virtualized list replaced A while scrolling.
  Observer.last.callback();
  await new Promise(resolve=>setTimeout(resolve,220));
  assert(events.some(update=>update.session===session.session && update.channels.some(row=>row.id===B)));
  stopBulkScan();

  global.document={createElement:fakeElement};
  const state={}; let failHost=true;
  const messages=event();
  const chromeMock={storage:{local:{async get(keys){return {...state};},async set(value){Object.assign(state,value);}}},
    runtime:{onMessage:messages,connectNative(){const onMessage=event(),onDisconnect=event();
      return {onMessage,onDisconnect,postMessage(payload){
        setTimeout(()=>failHost?onDisconnect.emit():onMessage.emit({ok:true,count:payload.records.length}),0);
      },disconnect(){}};}},
    tabs:{async query(){return [{id:1,url:url(A)}]},async sendMessage(id,message){
      return message.type==="diagnose_channel_menu" ? {first:diagnostic,last:diagnostic,dynamics:{}} :
        {session:"s",channels:[observation(A,"A")]};}}};
  const ui=()=>({status:fakeElement(),counts:fakeElement(),rows:fakeElement()});
  const firstUi=ui();
  const first=await createPanel(chromeMock,firstUi,{duration:1000,hostTimeout:50});
  first.merge([observation(A,"A")]);
  assert.match(firstUi.counts.textContent,/Mục menu lượt quét này: 0/);
  await first.start();
  assert.match(firstUi.counts.textContent,/Mục menu lượt quét này: 1/);
  assert.match(firstUi.counts.textContent,/Catalog tích lũy: 1/);
  const nameInput=firstUi.rows.children[0].children[0].children[0];
  nameInput.value="Edited in panel"; nameInput.fire("change");
  await first.save();
  assert.equal(state.studioUnsynced,true);
  assert.equal(state.studioCatalog.length,1);
  const reopened=await createPanel(chromeMock,ui(),{duration:1000,hostTimeout:50});
  assert.equal(reopened.getRows().length,1);
  assert.equal(reopened.getRows()[0].display_name,"Edited in panel");
  const beforeDiagnostic=JSON.stringify(state.studioCatalog);
  await reopened.diagnose();
  assert.equal(JSON.stringify(state.studioCatalog),beforeDiagnostic);
  reopened.merge([observation(A,"Changed DOM")]);
  assert.equal(reopened.getRows()[0].display_name,"Edited in panel");
  failHost=false;
  await reopened.save();
  assert.equal(state.studioUnsynced,false);
})().catch(error=>{console.error(error);process.exitCode=1;});
