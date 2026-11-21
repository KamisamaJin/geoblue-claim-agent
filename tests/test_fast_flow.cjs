const assert = require('node:assert/strict');
const test = require('node:test');
const factory = import('../skills/geoblue-claims/scripts/fast-flow.mjs');

function portal() {
  const events = [];
  const elements = new Map(), uploaded = new Set();
  let focused = null, selectAll = false;
  let finalClicks = 0;
  const locator = (kind,name) => {
    const key=kind+':'+String(name);
    if (!elements.has(key)) elements.set(key,{kind,name,value:'',x:10+elements.size*120,y:250,width:100,height:40});
    const element=elements.get(key);
    return {
      first(){return this;}, count:async()=>kind==='text'&&uploaded.has(name)||kind==='button'&&name==='Home'&&events.some(e=>e[0]==='click'&&e[2]==='app_header_hamburger_menu Hamburger Menu')?1:0,
      getAttribute:async()=>'',
      evaluate:async fn=>fn({value:element.value,innerText:name===undefined?'':String(name),getAttribute:attr=>attr==='role'?kind:attr==='aria-label'&&name!==undefined?String(name):null,getBoundingClientRect:()=>element,ownerDocument:{defaultView:{innerHeight:908}}}),
      click:async()=>{throw new Error('Use real pointer input, not semantic DOM clicking');},
      waitFor:async()=>{}, isEnabled:async()=>true,
    };
  };
  return {events, finalClicks:()=>finalClicks,
    goto:async()=>{throw new Error('Do not reload the authenticated portal');},url:async()=>'https://members.bcbsglobalsolutions.com/eclaim/fileaclaim/webPreliminaryQuestions/authorization?q=TEST-1',
    getAXState:async()=>'', scroll:async()=>{},
    click:async([x,y])=>{focused=[...elements.values()].find(e=>x===e.x+e.width/2&&y===e.y+e.height/2);assert(focused,'Pointer must target an observed control');events.push(['click',focused.kind,String(focused.name)]);if(focused.name==='Submit eClaim') finalClicks++;},
    pressKey:async(_,key)=>{selectAll=key==='super+a';events.push(['key',key]);},
    typeText:async(_,text)=>{assert(focused,'A real pointer must focus the input before typing');focused.value=selectAll?text:focused.value+text;selectAll=false;events.push(['type',text]);},
    playwright:{domSnapshot:async()=>'',locator:selector=>locator('union',selector),getByRole:(kind,{name})=>locator(kind,name),getByText:name=>locator('text',name),waitForEvent:async()=>({setFiles:async paths=>{events.push(['upload',paths]);paths.forEach(p=>uploaded.add(p.split('/').pop()));}})}};
}
const permit = {user_confirmed_final:true,attempt_id:'A1',claim_id:'C1',eclaim_id:'TEST-1',
  verify:async()=>({may_transmit:true,attempt_id:'A1',claim_id:'C1'})};

test('test and preparation cannot submit even with a production permit; default is test',async()=>{
  const {createGeoBlueFastRun} = await factory;
  for(const options of [{},{mode:'test'},{mode:'prepare'}]) {
    const page=portal(),run=createGeoBlueFastRun(page,options);
    await assert.rejects(run.submit(permit),/MODE: final Submit is prohibited/);
    assert.equal(page.events.length,0);
    assert.equal(run.report().final_submit_attempted,false);
    assert.throws(()=>Object.defineProperty(run,'mode',{value:'production'}));
  }
});
test('expired preparation budget stops before touching the portal',async()=>{
  const {createGeoBlueFastRun}=await factory;
  const page=portal(),run=createGeoBlueFastRun(page,{startedAt:Date.now()-180001});
  await assert.rejects(run.enter('Charge Amount,','10000'),/time budget exceeded/);
  assert.equal(page.events.length,0);
});
test('generic pointer helper cannot bypass final approval for submission, certification or signing',async()=>{
  const {createGeoBlueFastRun}=await factory;
  for (const mode of ['test','prepare']) {
    const page=portal(),run=createGeoBlueFastRun(page,{mode});
    await assert.rejects(run.point(run.role('button','Submit eClaim')),/guarded production/);
    await assert.rejects(run.point(run.role('checkbox','I certify')),/do not certify/);
    await assert.rejects(run.point(run.role('textbox')),/do not certify/);
    assert.equal(page.events.length,0);
  }
});
test('native input is checked after typing rather than assuming a semantic fill committed',async()=>{
  const {createGeoBlueFastRun}=await factory;
  const page=portal(),run=createGeoBlueFastRun(page);
  page.typeText=async()=>{};
  await assert.rejects(run.enter('Charge Amount,','10000'),/did not commit/);
  assert.equal(page.finalClicks(),0);
});
test('preparation batches native dropdown input and exact archived uploads without submit',async()=>{
  const {createGeoBlueFastRun} = await factory;
  const page=portal(),run=createGeoBlueFastRun(page);
  await run.step('charges',async()=>{
    await run.select('Country of Treatment dropdown','South','South Korea');
    await run.select('Billed Invoice Currency dropdown','Korea','Korean Republic Won (S.Korea)');
    await run.date('start','11/01/2026');
    await run.enter('Other Service description,','Synthetic outpatient visit');
    await run.upload('/private/tmp/synthetic-clinic.jpg','synthetic-clinic.jpg');
    await run.upload('/private/tmp/synthetic-pharmacy.jpg','synthetic-pharmacy.jpg','a'.repeat(64));
    await run.upload('/private/tmp/synthetic-pharmacy.jpg','synthetic-pharmacy.jpg','a'.repeat(64));
    await run.next();
  });
  assert.equal(page.finalClicks(),0);
  assert.equal(page.events.filter(e=>e[0]==='upload').length,2);
  assert(page.events.some(e=>e[0]==='type'&&e[1]==='Korea'));
  assert.equal(run.report().stages[0].ok,true);
  await assert.rejects(run.upload('/private/tmp/synthetic-pharmacy.jpg','synthetic-pharmacy.jpg','b'.repeat(64)),/filename alone/);
});
test('production requires guard and exact target; one final click maximum',async()=>{
  const {createGeoBlueFastRun} = await factory;
  const page=portal(),run=createGeoBlueFastRun(page,{mode:'production'});
  await assert.rejects(run.submit({...permit,eclaim_id:'WRONG'}),/does not match/);
  await assert.rejects(run.submit({...permit,user_confirmed_final:false}),/confirmation/);
  await assert.rejects(run.submit({...permit,verify:async()=>({may_transmit:false})}),/confirmation/);
  assert.equal(page.finalClicks(),0);
  await run.submit(permit);
  assert.equal(page.finalClicks(),1);
  await assert.rejects(run.submit(permit),/already attempted/);
  assert.equal(page.finalClicks(),1);
});
test('the complete preparation path stops before certification and final submit',async()=>{
  const {createGeoBlueFastRun}=await factory;
  const page=portal(),run=createGeoBlueFastRun(page);
  const payload={accident:false,other_insurance:false,category:'hospital_facility',patient:'Demo Child',
    facility:'SYNTHETIC CLINIC',facility_search:'SYNTHETIC',city:'Seoul',country_search:'South',country_option:'South Korea',
    currency_search:'Korea',currency_option:'Korean Republic Won (S.Korea)',condition:'Synthetic symptoms',
    service:'Synthetic visit',total:'10000',service_date_us:'11/01/2026',account_last4:'1234',
    attachments:[{path:'/private/tmp/synthetic.jpg',filename:'synthetic.jpg'}]};
  await run.prepareStart(payload);await run.prepareCharge(payload);await run.prepareFinish(payload);
  assert.equal(page.finalClicks(),0);
  assert.equal(page.events.filter(e=>e[0]==='click'&&e[1]==='checkbox').length,0);
  assert.equal(run.report().stages.length,3);
  assert(page.events.some(e=>e[0]==='click'&&e[2]==='Home'),'Use portal navigation to preserve the live session');
  await assert.rejects(run.prepareStart(payload),/already started/);
  assert.equal(page.finalClicks(),0);
});
test('real draft preparation uses normal names and still needs a separate production run',async()=>{
  const {createGeoBlueFastRun}=await factory;
  const page=portal(),run=createGeoBlueFastRun(page,{mode:'prepare'});
  const payload={accident:false,other_insurance:false,category:'hospital_facility',patient:'Demo Child',
    nickname:'Demo real draft',charge_nickname:'Demo outpatient care',
    facility:'SYNTHETIC CLINIC',facility_search:'SYNTHETIC',city:'Seoul',country_search:'South',country_option:'South Korea',
    currency_search:'Korea',currency_option:'Korean Republic Won (S.Korea)',condition:'Synthetic symptoms',
    service:'Synthetic visit',total:'10000',service_date_us:'11/01/2026',account_last4:'1234',
    attachments:[{path:'/private/tmp/synthetic.jpg',filename:'synthetic.jpg'}]};
  await run.prepareStart(payload);await run.prepareCharge(payload);await run.prepareFinish(payload);
  const typed=page.events.filter(e=>e[0]==='type').map(e=>e[1]);
  assert(typed.includes('Demo real draft')&&typed.includes('Demo outpatient care'));
  assert(!typed.some(value=>value.includes('TEST-DO-NOT-SUBMIT')));
  assert.equal(run.report().mode,'prepare');
  await assert.rejects(run.submit(permit),/PREPARE MODE/);
  assert.equal(page.finalClicks(),0);
});
test('a timeout after the final click cannot cause an automatic second click',async()=>{
  const {createGeoBlueFastRun} = await factory;
  const page=portal(),base=page.playwright.getByRole;
  page.playwright.getByRole=(kind,name)=>{
    const locator=base(kind,name);
    if(name.name==='Your eClaim was submitted successfully.') locator.waitFor=async()=>{throw new Error('Timeout');};
    return locator;
  };
  const run=createGeoBlueFastRun(page,{mode:'production'});
  await assert.rejects(run.submit(permit),/Timeout/);
  await assert.rejects(run.submit(permit),/already attempted/);
  assert.equal(run.report().final_submit_attempted,true);
});
