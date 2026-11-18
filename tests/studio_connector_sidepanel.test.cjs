const assert = require("node:assert/strict");
const {mergeCatalog, mergeRemoteCatalog, editName, markDuplicates} = require("../studio_connector_extension/catalog.js");
Object.assign(globalThis,{mergeCatalog,mergeRemoteCatalog,editName,markDuplicates});
const {startBulkScan, stopBulkScan, diagnoseChannelMenu, beginRegionTargeting,
  cancelRegionTargeting, confirmRegion, inNavigationDrawer, visibleSwitcherEntry} =
  require("../studio_connector_extension/content.js");
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
  let localCandidate=mergeCatalog([], [{id:"",name:"日本語の候補",handle:"@candidate",
    source_url:"https://studio.youtube.com/",evidence:"visible_switcher_item"}]);
  localCandidate=editName(localCandidate,localCandidate[0].key,"編集した名前");
  const remoteCandidate={...localCandidate[0],id:B,id_source:"user_supplied",
    status:"identity_user_supplied"};
  localCandidate=mergeRemoteCatalog(localCandidate,[remoteCandidate]);
  assert.equal(localCandidate[0].id,B);
  assert.equal(localCandidate[0].display_name,"編集した名前");
  localCandidate=mergeCatalog(localCandidate,[{id:"",name:"日本語の候補",handle:"@candidate",
    source_url:"https://studio.youtube.com/",evidence:"visible_switcher_item"}]);
  assert.equal(localCandidate.length,1);
  assert.equal(localCandidate[0].id_source,"user_supplied");
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
  let items=[];
  const entry=(name,id)=>({tagName:"YTCP-ACCOUNT-ITEM",className:"channel-row",innerText:name,
    getClientRects:()=>[1],getAttribute:()=>null,querySelector:()=>null,
    closest:()=>({href:url(id)}),getBoundingClientRect:()=>({left:10,top:10,width:200,height:40})});
  const menu={tagName:"DIV",className:"account-list",children:items,
    getClientRects:()=>[1],getAttribute:()=>null,getBoundingClientRect:()=>({left:5,top:5,width:250,height:300}),
    querySelectorAll:()=>items,addEventListener(){},removeEventListener(){}};
  const handlers={};
  const doc={documentElement:{append(){}},createElement:()=>({style:{},setAttribute(){},remove(){}}),
    querySelectorAll:()=>[],addEventListener(type,callback){handlers[type]=callback;},
    removeEventListener(type){delete handlers[type];}};
  class Observer { constructor(callback){this.callback=callback;Observer.last=this;} observe(){} disconnect(){} }
  assert.throws(()=>startBulkScan(doc,()=>url(A),update=>events.push(update),1000,Observer),/Chọn vùng/);
  items=[entry("Channel A",A),entry("Channel B",B)]; menu.children=items;
  items.forEach(item=>item.parentElement=menu);
  beginRegionTargeting(doc,update=>events.push(update),1000);
  let openerPrevented=false;
  handlers.click({target:{tagName:"BUTTON"},composedPath:()=>[{tagName:"BUTTON"},doc],
    preventDefault(){openerPrevented=true;},stopImmediatePropagation(){}});
  assert(!openerPrevented); // The switcher can be reopened after Side Panel takes focus.
  let earlyStopped=false;
  handlers.pointerdown({target:items[0],composedPath:()=>[items[0],menu,doc],
    stopImmediatePropagation(){earlyStopped=true;}});
  assert(earlyStopped);
  let prevented=false, stopped=false;
  handlers.click({target:items[0],composedPath:()=>[items[0],menu,doc],
    preventDefault(){prevented=true;},stopImmediatePropagation(){stopped=true;}});
  assert(prevented && stopped); // Selection click cannot switch channels.
  assert(events.some(update=>update.type==="target_region_proposed" && update.candidates.length));
  const confirmed=confirmRegion(0);
  assert.equal(confirmed.diagnostic.visible_matching_rows,2);
  assert(!handlers.click); // All temporary click interceptors removed.
  const session=startBulkScan(doc,()=>url(A),update=>events.push(update),1000,Observer);
  assert.equal(session.channels.length,2);
  items=[entry("Channel C",B),entry("No ID","")]; // Virtualized rows changed during scroll.
  menu.children=items; items.forEach(item=>item.parentElement=menu);
  Observer.last.callback();
  await new Promise(resolve=>setTimeout(resolve,220));
  assert(events.some(update=>update.session===session.session && update.channels.some(row=>row.id===B)));
  assert(events.some(update=>update.session===session.session && update.channels.some(row=>!row.id)));
  stopBulkScan();
  beginRegionTargeting(doc,update=>events.push(update),1000);
  cancelRegionTargeting();
  assert(!handlers.click && !handlers.pointerdown);
  const drawer={matches:selector=>selector.includes("ytcp-navigation-drawer")};
  const navRow={...entry("Dashboard",A),parentNode:drawer};
  assert(inNavigationDrawer(navRow));
  assert.equal(visibleSwitcherEntry(navRow,1),null);
  const navMenu={tagName:"UL",id:"main-menu",className:"menu",matches:selector=>selector.includes("ul#main-menu"),
    getClientRects:()=>[1],getAttribute:()=>"menu",querySelectorAll:()=>[navRow],children:[navRow]};
  navRow.parentNode=navMenu;
  assert.equal(diagnoseChannelMenu({querySelectorAll:()=>[navMenu,navRow]}).candidate_count,0);
  const firstRows=mergeCatalog([],session.channels);
  const scrollUpdate=events.find(update=>update.session===session.session &&
    update.channels.some(row=>!row.id));
  const accumulated=mergeCatalog(firstRows,scrollUpdate.channels);
  assert(accumulated.some(row=>row.id===A));
  assert(accumulated.some(row=>!row.id));

  global.document={createElement:fakeElement};
  const state={}; let failHost=true, teRecords=[];
  const messages=event();
  const chromeMock={storage:{local:{async get(keys){return {...state};},async set(value){Object.assign(state,value);}}},
    runtime:{onMessage:messages,connectNative(){const onMessage=event(),onDisconnect=event();
      return {onMessage,onDisconnect,postMessage(payload){
        if (payload.type==="catalog_sync" && !failHost) teRecords=payload.records;
        setTimeout(()=>failHost?onDisconnect.emit():onMessage.emit(payload.type==="catalog_get" ?
          {ok:true,count:teRecords.length,records:teRecords} :
          {ok:true,count:payload.records.length}),0);
      },disconnect(){}};}},
    tabs:{async query(){return [{id:1,url:url(A)}]},async sendMessage(id,message){
      return message.type==="diagnose_channel_menu" ? {first:diagnostic,last:diagnostic,dynamics:{}} :
        message.type==="begin_region_targeting" ? {ok:true} :
        message.type==="confirm_region" ? {ok:true,diagnostic:confirmed.diagnostic} :
        message.type==="cancel_region_targeting" ? {ok:true} :
        {session:"s",channels:[observation(A,"A")]};}}};
  const ui=()=>({status:fakeElement(),counts:fakeElement(),rows:fakeElement(),
    regionChoices:fakeElement(),diagnostic:fakeElement()});
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
  await reopened.target();
  messages.emit({type:"target_region_proposed",candidates:[confirmed.region]},{tab:{id:1}});
  await reopened.confirmTarget();
  assert.match(JSON.stringify(reopened.getRows()),/Edited in panel/);
  assert.equal(JSON.stringify(state.studioCatalog),beforeDiagnostic);
  await reopened.diagnose();
  assert.equal(JSON.stringify(state.studioCatalog),beforeDiagnostic);
  reopened.merge([observation(A,"Changed DOM")]);
  assert.equal(reopened.getRows()[0].display_name,"Edited in panel");
  failHost=false;
  await reopened.save();
  assert.equal(state.studioUnsynced,false);
  assert.equal(teRecords.length,1);
  teRecords.push({...remoteCandidate,key:"candidate:from-te"});
  const afterTeEdit=await createPanel(chromeMock,ui(),{duration:1000,hostTimeout:50});
  assert.equal(afterTeEdit.getRows().find(row=>row.key==="candidate:from-te").id,B);
  assert.equal(afterTeEdit.getRows().find(row=>row.key==="candidate:from-te").id_source,"user_supplied");
  await afterTeEdit.save();
  assert.equal(teRecords.find(row=>row.key==="candidate:from-te").id,B);
  assert.equal(teRecords.length,2);
})().catch(error=>{console.error(error);process.exitCode=1;});
