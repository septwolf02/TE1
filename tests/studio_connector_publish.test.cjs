const assert=require('node:assert/strict');
Object.assign(globalThis,require('../studio_connector_extension/upload.js'));
const {uploadDialog,textField,thumbnailInput,blockingAlert,privateRow,completePrivateUpload}=
  require('../studio_connector_extension/studio_publish.js');
const A='UC'+'a'.repeat(22), B='UC'+'b'.repeat(22), V='v'.repeat(11);
const title='大谷翔平速報 — 完全版';
const description='一行目。\n二行目：日本語の説明。';
const text=value=>({nodeName:'#text',nodeValue:value});
const node=(id,name,attributes=[],children=[])=>({nodeId:id,nodeName:name,attributes,children});
const task={request_id:'a'.repeat(32),channel_id:A,stage:'file_selected',title,description,
  tags:'optional',audience:'not_made_for_kids',thumbnail_path:'C:\\TE\\thumb.png',
  thumbnail_name:'thumb.png',thumbnail_bytes:123};
const dialog=stage=>node(2,'YTCP-UPLOADS-DIALOG',[],[
  node(20,'H2',[],[text(`Step ${stage}`)]),
  ...(stage===0?[
    node(3,'INPUT',['placeholder','Title']),
    node(4,'TEXTAREA',['placeholder','Description']),
    node(5,'INPUT',['type','file','accept','image/png','hidden','']),
    node(6,'INPUT',['role','radio','aria-label',"No, it's not made for kids"]),
  ]:[]),
  ...(stage<4?[node(7,'BUTTON',[],[text('Next')])]:[
    node(8,'INPUT',['role','radio','aria-label','Private']),
    node(9,'BUTTON',[],[text('Save')])])]);
const row=node(11,'TR',['role','row'],[
  node(12,'A',['href',`https://www.youtube.com/watch?v=${V}`],[text(title)]),
  node(13,'SPAN',[],[text('Private')])]);
(async()=>{
  assert(uploadDialog(node(1,'#document',[],[dialog(0)])));
  assert.equal(textField(dialog(0),'title').nodeId,3);
  assert.equal(thumbnailInput(dialog(0)).nodeId,5);
  assert.equal(privateRow(node(1,'#document',[],[row]),title),V);
  assert.equal(privateRow(node(1,'#document',[],[row]),'Different title'),'');
  const noPrivate=structuredClone(row);noPrivate.children[1].children[0].nodeValue='Public';
  assert.equal(privateRow(noPrivate,title),'');
  const duplicate=structuredClone(row);
  duplicate.children[0].attributes[1]='https://www.youtube.com/watch?v='+'x'.repeat(11);
  assert.equal(privateRow(node(1,'#document',[],[row,duplicate]),title),'');
  const checks=node(30,'YTCP-UPLOADS-DIALOG',[],[
    node(31,'H2',[],[text('Checks')]),node(32,'DIV',['role','alert'],[text('Copyright claim')])]);
  assert(blockingAlert(checks));
  const unknownChecks=node(30,'YTCP-UPLOADS-DIALOG',[],[node(31,'H2',[],[text('Kiểm tra')])]);
  assert(blockingAlert(unknownChecks));
  unknownChecks.children.push(node(32,'DIV',['role','status'],[text('Không phát hiện vấn đề')]));
  assert.equal(blockingAlert(unknownChecks),false);
  let stage=0,saved=false,content=false,tabUrl=`https://studio.youtube.com/channel/${A}/videos/upload`;
  let selectedThumb=false,formReads=0;const fields={},steps=[],calls=[];
  const chrome={tabs:{async get(){return {url:tabUrl}},async update(_id,options){
    assert.equal(options.url,`https://studio.youtube.com/channel/${A}/videos`);
    content=true;tabUrl=options.url}},debugger:{async attach(){calls.push('attach')},
    async detach(){calls.push('detach')},async sendCommand(_target,method,params){
      if(method==='DOM.getDocument') {
        const form=dialog(stage);
        if(stage===0 && formReads++<2) form.children=form.children.filter(item=>item.nodeId!==4);
        return {root:node(1,'#document',[],content?[row]:saved?[]:[form])};
      }
      if(method==='Accessibility.getFullAXTree') return {nodes:[]};
      if(method==='DOM.resolveNode') return {object:{objectId:String(params.nodeId)}};
      if(method==='DOM.setFileInputFiles') {assert.equal(params.nodeId,5);
        assert.deepEqual(params.files,[task.thumbnail_path]);selectedThumb=true;return {}};
      if(method==='Runtime.callFunctionOn') {
        const id=Number(params.objectId),body=params.functionDeclaration;
        if(body.includes('const f=this.files')) return {result:{value:{name:'thumb.png',size:123}}};
        if(body.includes('this.focus()')) {fields[id]=params.arguments[0].value;
          return {result:{value:fields[id]}}};
        if(body.includes("return 'value' in this")) return {result:{value:fields[id]}};
        if(body.includes('this.checked===true')) return {result:{value:id===6||id===8}};
        if(body.includes('this.click()')) {
          if(id===7) stage++;
          if(id===9) saved=true;
          return {result:{value:true}};
        }
      }
      throw new Error(method);
    }}};
  const result=await completePrivateUpload(chrome,42,task,async step=>steps.push(step),
    {pause:async()=>{},pollMs:0});
  assert.equal(fields[3],title);assert.equal(fields[4],description);
  assert(formReads>=3);
  assert(selectedThumb);assert(saved);assert.equal(stage,4);
  assert.deepEqual(result,{video_id:V,page_url:tabUrl,visibility:'private',tags_applied:false});
  assert(steps.includes('saving_private')&&steps.at(-1)==='verifying_video');
  assert.equal(calls.at(-1),'detach');
  const clickCount=calls.length;
  const resumed=await completePrivateUpload(chrome,42,{...task,stage:'saving'},
    async step=>steps.push(step),{pause:async()=>{},pollMs:0});
  assert.equal(resumed.video_id,V);
  assert(!calls.slice(clickCount).includes('Runtime.callFunctionOn')); // No repeat Save click.
  tabUrl=`https://studio.youtube.com/channel/${B}/videos`;
  await assert.rejects(completePrivateUpload(chrome,42,task,async()=>{},
    {pause:async()=>{},pollMs:0}),/Channel ID/);
  assert.equal(calls.at(-1),'detach');
  const partial=structuredClone(dialog(0));partial.children=partial.children.filter(n=>n.nodeId!==4);
  assert.equal(textField(partial,'description'),null);
})().catch(error=>{console.error(error);process.exitCode=1});
