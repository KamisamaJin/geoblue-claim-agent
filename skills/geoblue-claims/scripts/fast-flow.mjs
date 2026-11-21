// Load in cua_repl, never in a terminal browser driver.
// Use prepare for real drafts, test for rehearsals, production only after final approval.
export function createGeoBlueFastRun(tab, options = {}) {
  const mode = options.mode || 'test';
  if (!['test', 'prepare', 'production'].includes(mode)) throw new Error('Invalid run mode');
  const origin = 'https://members.bcbsglobalsolutions.com';
  const started = options.startedAt || Date.now();
  const budgetMs = options.budgetMs || 180000;
  const timings = [];
  const uploaded = new Map((options.uploadedAttachments||[]).map(a=>[a.filename,a]));
  let finalClicked = false;
  let startedPreparation = false;
  const role = (kind, name) => tab.playwright.getByRole(kind, {name, exact: typeof name === 'string'});
  async function state() {
    await tab.getAXState({emit:false});
    await tab.playwright.getByText('Loading',{exact:true}).waitFor({state:'hidden',timeout:20000});
    return tab.playwright.domSnapshot();
  }
  async function assertOrigin() {
    if (Date.now()-started>=budgetMs) throw new Error('Preparation time budget exceeded; preserve the unsubmitted draft and report the blocker');
    const url = await tab.url();
    if (new URL(url).origin !== origin || new URL(url).pathname.includes('/login')) {
      throw new Error('Official authenticated portal session required');
    }
    return url;
  }
  async function step(name, body, expected) {
    await assertOrigin();
    const start = Date.now();
    try {
      await body();
      if (expected) await expected.waitFor({state:'visible', timeout:15000});
      await state();
      timings.push({stage:name, milliseconds:Date.now()-start, ok:true});
      return {stage:name, url:await tab.url(), elapsed_ms:Date.now()-started};
    } catch (error) {
      timings.push({stage:name, milliseconds:Date.now()-start, ok:false});
      throw error; // No automatic retry of a mutation or final submit.
    }
  }
  async function bring(input) {
    for (let tries=0;tries<3;tries++) {
      const r=await input.evaluate(el=>{const b=el.getBoundingClientRect();return {y:b.y,height:b.height,viewport:el.ownerDocument.defaultView.innerHeight};});
      if (r.y>=80 && r.y+r.height<=r.viewport-80) return;
      await tab.scroll([400,450],r.y<80?'up':'down',Math.min(3,Math.max(0.2,Math.abs(r.y-300)/r.viewport)));
      await state();
    }
    throw new Error('Control did not enter the visible viewport; preserve this draft');
  }
  async function point(input) {
    await assertOrigin();
    await state();
    const action=await input.evaluate(el=>({label:el.getAttribute('aria-label')||el.innerText||el.textContent||'',role:el.getAttribute('role')||el.type||''}));
    if (/^Submit eClaim\b/i.test(action.label.trim())) throw new Error('Final Submit must use the guarded production entrypoint');
    if (mode!=='production' && (action.role==='checkbox' ||
        new URL(await tab.url()).pathname.endsWith('/authorization') &&
        ['text','textbox'].includes(action.role) && !action.label.trim())) {
      throw new Error('Test and preparation runs do not certify or sign');
    }
    await bring(input);
    const r=await input.evaluate(el=>{const b=el.getBoundingClientRect();return {x:b.x,y:b.y,width:b.width,height:b.height};});
    await tab.click([r.x+r.width/2,r.y+r.height/2]);
    await state();
  }
  async function radio(name) {
    const input=role('radio',name);
    if (await input.getAttribute('aria-checked')!=='true') await point(input);
  }
  async function select(field, query, option) {
    await assertOrigin();
    const input=role('textbox',field);
    await point(input);
    await role('searchbox').waitFor({state:'visible',timeout:3000});
    if (query) {
      await point(role('searchbox'));
      await tab.pressKey(null,'super+a');
      await tab.typeText(null, query);
      await state();
    }
    const choice = role('button', option);
    await choice.waitFor({state:'visible',timeout:5000});
    await point(choice);
    await role('searchbox').waitFor({state:'hidden',timeout:3000});
    await state();
  }
  async function enter(field, text) {
    await assertOrigin();
    const input = role('textbox',field);
    await point(input);
    await tab.pressKey(null,'super+a');
    await tab.typeText(null,text);
    await state();
    const actual=await input.evaluate(el=>el.value);
    if (actual!==text) throw new Error('Input did not commit to '+field+'; preserve the existing draft');
  }
  async function date(which, value) {
    await assertOrigin();
    const picker=role('button',new RegExp(`^${which} date service picker date picker`));
    if ((await picker.getAttribute('aria-label')||'').includes('Current value: '+value+' ')) return;
    await point(picker);
    const search=role('searchbox','mm/dd/yyyy');
    await search.waitFor({state:'visible',timeout:3000});
    await state();
    await point(search);
    await tab.pressKey(null,'super+a');
    await tab.typeText(null,value);
    await point(role('button','OK'));
    await search.waitFor({state:'hidden',timeout:3000});
    await state();
  }
  async function next(expected) {
    await assertOrigin();
    await tab.scroll([600,735],'down',2);
    await state();
    await point(role('button','Next'));
    if (expected) await expected.waitFor({state:'visible',timeout:15000});
    await state();
  }
  async function upload(path, filename, sha256) {
    await assertOrigin();
    if (await tab.playwright.getByText(filename,{exact:true}).count()) {
      const known=uploaded.get(filename);
      if (known && known.path===path && sha256 && known.sha256===sha256) return;
      throw new Error('Existing attachment requires matching saved upload evidence; filename alone cannot prove its contents');
    }
    await point(tab.playwright.getByText('Add Supporting Documents',{exact:true}));
    await role('button','Upload document').waitFor({state:'visible',timeout:15000});
    await state();
    const chooserPending = tab.playwright.waitForEvent('filechooser',{timeout:10000});
    await point(role('button','Upload document'));
    const chooser = await chooserPending;
    await chooser.setFiles([path]);
    await tab.playwright.getByText(filename,{exact:true}).waitFor({state:'visible',timeout:20000});
    await state();
    uploaded.set(filename,{path,filename,sha256:sha256||null});
  }
  async function prepareStart(p) {
    if (startedPreparation) throw new Error('Preparation already started; resume the existing draft');
    if (!p || p.accident !== false || p.other_insurance !== false || p.category !== 'hospital_facility') {
      throw new Error('Fast path requires confirmed no-accident/no-other-insurance and Hospital / Facility answers');
    }
    for (const field of ['patient','facility','facility_search','city','country_search','country_option',
                         'currency_search','currency_option','condition','service','total','service_date_us']) {
      if (typeof p[field] !== 'string' || !p[field].trim()) throw new Error('Missing confirmed field: '+field);
    }
    if (!/^\d{4}$/.test(p.account_last4) || !Array.isArray(p.attachments) || !p.attachments.length ||
        p.attachments.some(a=>!a.path || !a.filename)) throw new Error('Confirmed saved account and archived attachments required');
    startedPreparation = true;
    return step('entry-basic-insurance',async()=>{
      const current=new URL(await assertOrigin());
      if (p.resume_eclaim_id) {
        if (!current.pathname.endsWith('/webPreliminaryQuestions') || current.searchParams.get('q')!==p.resume_eclaim_id) {
          throw new Error('Resume requires the existing named test draft at Preliminary Questions');
        }
      } else {
        if (current.pathname !== '/') {
          // Keep the live SPA/session. A full reload can discard its auth state.
          const home=role('button','Home');
          if (!await home.count()) {
            await point(role('button','app_header_hamburger_menu Hamburger Menu'));
            try {
              await home.waitFor({state:'visible',timeout:1500});
            } catch (error) {
              await state();
              if (!await tab.playwright.getByText('Home',{exact:true}).count()) throw error;
            }
          }
          await point(await home.count()?home:tab.playwright.getByText('Home',{exact:true}));
          await role('button','File a new eClaim').waitFor({state:'visible',timeout:15000});
          await state();
        }
        await point(role('button','File a new eClaim'));
        await role('button','Get started').first().waitFor({state:'visible',timeout:10000});
        await state();
        await point(role('button','Get started').first());
      }
      if (!await role('button',/^Selected PRIMARY MEMBER/).count()) {
        await role('button','PRIMARY MEMBER Claims Reimbursement will be paid to the Primary Member').waitFor({state:'visible',timeout:10000});
        await point(role('button','PRIMARY MEMBER Claims Reimbursement will be paid to the Primary Member'));
      }
      await role('button',/^Selected PRIMARY MEMBER/).waitFor({state:'visible',timeout:3000});
      await state();
      if (!await role('button',/^Selected BANK WIRE/).count()) await point(role('button','BANK WIRE TRANSFER OR ACH PAYMENT Wire transfer require important banking details'));
      await role('button',/^Selected BANK WIRE/).waitFor({state:'visible',timeout:3000});
      await state();
      await radio('No is Accident no');
      await next(tab.playwright.locator('[role="dialog"], [aria-label="Patient dropdown"]'));
      if (await role('button','Close').count()) await point(role('button','Close'));
      await role('textbox','Patient dropdown').waitFor({state:'visible',timeout:3000});
      if (p.resume_eclaim_id && new URL(await tab.url()).searchParams.get('q')!==p.resume_eclaim_id) {
        throw new Error('Portal did not preserve the existing draft reference; no replacement draft will be filed');
      }
      await state();
      if (mode==='test' || p.nickname) await enter('eClaim Nickname,',mode==='test'?'TEST-DO-NOT-SUBMIT-FAST':p.nickname);
      await point(role('textbox','Patient dropdown'));
      await role('button',p.patient).waitFor({state:'visible',timeout:3000});
      await state();
      await point(role('button',p.patient));
      await role('button',p.patient).waitFor({state:'hidden',timeout:3000});
      await state();
      await next(role('radio','No'));
      await radio('No');
      await next(role('textbox','Charge Nickname,'));
    });
  }
  async function prepareCharge(p) {
    return step('charge',async()=>{
      await radio('Hospital / Facility Hospital / Facility');
      await state();
      if (mode==='test' || p.charge_nickname) await enter('Charge Nickname,',mode==='test'?'TEST-DO-NOT-SUBMIT':p.charge_nickname);
      await select('Select Facility dropdown',p.facility_search,new RegExp('^'+p.facility.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')+'$','i'));
      // Reuse an observed saved facility's city/country/currency only when its
      // current autofill was visually checked against this confirmed payload.
      if (!p.saved_location_visually_verified) {
        await enter('City of Treatment,',p.city);
        await select('Country of Treatment dropdown',p.country_search,p.country_option);
      }
      await select('Condition or Diagnosis dropdown','OTHER','OTHER');
      await select('Service Description dropdown','OTHER','OTHER');
      await date('start',p.service_date_us);
      await date('end',p.service_date_us);
      if (!p.saved_currency_visually_verified) await select('Billed Invoice Currency dropdown',p.currency_search,p.currency_option);
      await enter('Other Condition or Diagnosis Description,',p.condition);
      await enter('Other Service description,',p.service);
      await enter('Charge Amount,',p.total);
      await tab.scroll([600,735],'down',3);
      await state();
      await role('button','Save charge').waitFor({state:'visible',timeout:3000});
      await point(role('button','Save charge'));
    },tab.playwright.getByText('Add Supporting Documents',{exact:true}));
  }
  async function prepareFinish(p) {
    return step('uploads-reimbursement-authorization',async()=>{
      for (const attachment of p.attachments) await upload(attachment.path,attachment.filename,attachment.sha256);
      await next(role('textbox','Payment Account Number dropdown'));
      await point(role('textbox','Payment Account Number dropdown'));
      const account=role('button',new RegExp('\\*+'+p.account_last4+'$'));
      await account.waitFor({state:'visible',timeout:3000});
      await state();
      await point(account);
      await account.waitFor({state:'hidden',timeout:3000});
      await state();
      await next(role('button',/^Submit eClaim/));
      // Preparation stops here in every mode; final approval is a separate action.
    });
  }
  async function submit(permit) {
    // This check precedes ALL UI access and is immutable for this run.
    if (mode !== 'production') throw new Error(mode.toUpperCase()+' MODE: final Submit is prohibited');
    if (finalClicked) throw new Error('Final submit already attempted; reconcile instead of retrying');
    if (!permit || typeof permit.verify !== 'function') throw new Error('Fresh local submission guard required');
    const guard = await permit.verify();
    if (!guard.may_transmit || guard.attempt_id !== permit.attempt_id ||
        guard.claim_id !== permit.claim_id || !permit.user_confirmed_final) {
      throw new Error('Specific final confirmation and matching guarded attempt required');
    }
    const url = new URL(await assertOrigin());
    if (!url.pathname.endsWith('/authorization') || url.searchParams.get('q') !== permit.eclaim_id) {
      throw new Error('Final page does not match the confirmed eClaim');
    }
    const button = role('button','Submit eClaim');
    await bring(button);
    if (!await button.isEnabled()) throw new Error('Final button is disabled');
    const r=await button.evaluate(el=>{const b=el.getBoundingClientRect();return {x:b.x,y:b.y,width:b.width,height:b.height};});
    finalClicked = true; // A timeout after this point is an unknown outcome.
    await tab.click([r.x+r.width/2,r.y+r.height/2]);
    await role('heading','Your eClaim was submitted successfully.').waitFor({state:'visible',timeout:20000});
    return state();
  }
  return Object.freeze({mode, role, step, point, radio, select, enter, date, next, upload, prepareStart, prepareCharge, prepareFinish, submit,
    report:() => ({mode, started_at:new Date(started).toISOString(), elapsed_ms:Date.now()-started,budget_ms:budgetMs,
                  stages:timings.slice(),uploads:[...uploaded.values()],final_submit_attempted:finalClicked})});
}
