// Offline browser, synthetic IDs and token. No real Telegram request.
const {chromium}=require('../../chrome-manager/frontend/node_modules/playwright');
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const frontend=path.resolve(__dirname,'../frontend'),origin='http://127.0.0.1:8000';
const secret='12345:FAKE_TELEGRAM_TOKEN_SECRET_123456';
(async()=>{
 const browser=await chromium.launch({channel:'chrome',headless:true});
 try{
  const context=await browser.newContext({offline:true,serviceWorkers:'block',viewport:{width:1100,height:1000}}),page=await context.newPage();
  const writes=[],outside=[],errors=[];let failSave=false;
  let snapshot={enabled:false,configured:false,token_saved:false,revision:1,chat_id:null,allowed_user_ids:[],bot:{id:null,username:null},check:{ok:false,status:'not_checked',reason:'credentials_required',checked_at:null,expires_at:null},gate:{required:true,gate_revision:2},pending:{items:[],total:0},uncertain_notifications:0};
  await page.route('**/*',async route=>{
   const req=route.request(),url=new URL(req.url());
   if(url.origin===origin&&url.pathname==='/auth/api/me')return route.fulfill({json:{user:{id:1,username:'fixture',role:'admin',must_change_password:false},csrf_token:'fixture-via-csrf'}});
   if(url.origin===origin&&/^(?:\/pages)?\/api\//.test(url.pathname)&&!['GET','HEAD','OPTIONS'].includes(req.method()))assert.equal(req.headers()['x-via-csrf'],'fixture-via-csrf');

   if(url.origin!==origin){outside.push(url.href);return route.abort()}
   if(url.pathname==='/')return route.fulfill({contentType:'text/html',body:'<!doctype html><html><head><link rel="stylesheet" href="/static/styles.css"><link rel="stylesheet" href="/static/idea-pipeline.css"></head><body><main style="max-width:850px;margin:30px auto"><script type="module">import {createTelegramApproval} from "/static/telegram-approval.js";window.panel=createTelegramApproval();document.querySelector("main").append(panel.node)</script></main></body></html>'});
   if(url.pathname.startsWith('/static/')){
    const file=path.resolve(frontend,url.pathname.slice(8));assert.ok(file.startsWith(frontend+path.sep));
    return route.fulfill({path:file,contentType:file.endsWith('.js')?'application/javascript':'text/css'});
   }
   if(url.pathname==='/api/session')return route.fulfill({json:{csrf_token:'fixture-telegram-ui',app:{}}});
   if(url.pathname==='/api/telegram-approval'&&req.method()==='GET')return route.fulfill({json:snapshot});
   assert.equal(req.headers()['x-mpm-csrf'],'fixture-telegram-ui');writes.push({method:req.method(),path:url.pathname,body:req.postDataJSON()});
   if(url.pathname==='/api/telegram-approval'&&req.method()==='PUT'){
    if(failSave)return route.abort();
    const body=req.postDataJSON();assert.equal(body.revision,snapshot.revision);
    snapshot={...snapshot,revision:snapshot.revision+1,enabled:body.enabled,chat_id:body.chat_id,allowed_user_ids:body.allowed_user_ids,token_saved:true,configured:true};
    return route.fulfill({json:snapshot});
   }
   if(url.pathname==='/api/telegram-approval/check'){
    snapshot={...snapshot,check:{ok:true,status:'ready',reason:'ready',checked_at:new Date().toISOString(),expires_at:new Date(Date.now()+300000).toISOString()},bot:{id:'12345',username:'fixture_bot'}};
    return route.fulfill({json:snapshot});
   }
   if(url.pathname==='/api/telegram-approval/trial'){
    const body=req.postDataJSON();assert.deepEqual(body,{revision:snapshot.revision,request_id:snapshot.pending.items[0].request_id});
    snapshot={...snapshot,trial:{request_id:body.request_id,state:'active',active:true,delivery_state:'sent',expires_at:new Date(Date.now()+1800000).toISOString()}};
    return route.fulfill({json:snapshot});
   }
   throw Error('Unexpected fixture route '+url.pathname);
  });
  page.on('pageerror',e=>errors.push(e.message));await page.goto(origin+'/');
  assert.equal(writes.length,0);
  await page.getByRole('button',{name:'Đọc lại trạng thái',exact:true}).click();
  await page.getByRole('list',{name:'Từ duyệt đến đăng',exact:true}).getByText('Video chờ trong lịch nội bộ Via.',{exact:false}).waitFor();
  await page.getByText('Đăng Page đang bắt buộc duyệt.',{exact:false}).waitFor();
  assert.equal(await page.getByRole('checkbox',{name:'Bật nhận duyệt Telegram',exact:true}).isDisabled(),true);
  const token=page.getByLabel('Token bot Telegram');assert.equal(await token.getAttribute('type'),'password');
  await token.fill(secret);await page.getByLabel('Chat ID Telegram').fill('-100123');await page.getByLabel('User ID được phép duyệt').fill('99, 100');
  await page.getByRole('button',{name:'Lưu cấu hình',exact:true}).click();
  await page.getByText('Có · được mã hóa trên máy',{exact:true}).waitFor();assert.equal(await token.inputValue(),'');
  assert.equal(writes.length,1);assert.equal(writes[0].body.bot_token,secret);assert.equal(writes[0].body.enabled,false);
  await page.getByRole('button',{name:'Kiểm tra bot',exact:true}).click();await page.getByText('@fixture_bot',{exact:true}).waitFor();
  await page.getByRole('checkbox',{name:'Bật nhận duyệt Telegram',exact:true}).check();await page.getByRole('button',{name:'Lưu cấu hình',exact:true}).click();
  await page.getByText('Đã bật nhận duyệt',{exact:true}).waitFor();assert.equal(writes[2].body.enabled,true);assert.equal('bot_token' in writes[2].body,false);
  failSave=true;await page.getByRole('checkbox',{name:'Bật nhận duyệt Telegram',exact:true}).uncheck();await page.getByRole('button',{name:'Lưu cấu hình',exact:true}).click();
  await page.getByText('Chưa rõ kết quả. Đọc lại trạng thái trước khi thao tác tiếp.',{exact:true}).waitFor();
  assert.equal(await page.getByRole('button',{name:'Lưu cấu hình',exact:true}).isDisabled(),true);
  const count=writes.length;await page.waitForTimeout(100);assert.equal(writes.length,count);
  await page.getByRole('button',{name:'Đọc lại trạng thái',exact:true}).click();await page.getByText('Đã bật nhận duyệt',{exact:true}).waitFor();
  // The enabled badge survives the read-in-progress render. Wait for the
  // completed GET to release the mutation latch, not that existing badge.
  await page.waitForFunction(()=>[...document.querySelectorAll('button')].some(node=>node.textContent==='Lưu cấu hình'&&!node.disabled));
  assert.equal(await page.getByRole('button',{name:'Lưu cấu hình',exact:true}).isDisabled(),false);
  const beforeDates=writes.length,instant='2031-01-01T00:30:00Z';snapshot={...snapshot,pending:{total:2,items:[{request_id:'12345678-1234-4123-8123-123456789abc',approve_by:instant,binding:{queue:'token',page_id:'555',caption:'Fixture',scheduled_at:instant,timezone:'America/Los_Angeles'}},{request_id:'12345678-1234-4123-8123-123456789abd',approve_by:instant,binding:{queue:'chrome',page_id:'556',caption:'Fixture',scheduled_at:instant,timezone:'UTC'}}]}};
  await page.evaluate(value=>panel.adopt(value),snapshot);await page.getByRole('heading',{name:'Video chờ duyệt · 2',exact:true}).waitFor();await page.locator('.telegram-pending').first().locator('summary').click();await page.locator('.telegram-pending').last().locator('summary').click();
  for(const line of ['Lịch: 31/12/2030 16:30 (America/Los_Angeles)','Hạn duyệt: 31/12/2030 16:30 (America/Los_Angeles)','Lịch: 01/01/2031 00:30 (UTC)','Hạn duyệt: 01/01/2031 00:30 (UTC)'])await page.getByText(line,{exact:true}).waitFor();
  assert.equal(snapshot.pending.items[0].binding.scheduled_at,instant,'Formatting leaves frozen ISO binding unchanged');assert.equal(writes.length,beforeDates,'Displaying date, zone and approval deadline never sends or approves');assert.equal(await page.locator('.telegram-pending button:enabled,.telegram-pending input,video[autoplay]').count(),0,'Global mode cannot launch a trial or grant any approval');
  snapshot={...snapshot,pending:{...snapshot.pending,items:snapshot.pending.items.map((item,index)=>index?item:{...item,binding:{...item.binding,publish_mode:'on_approval',approve_by:instant}})}};
  await page.evaluate(value=>panel.adopt(value),snapshot);await page.locator('.telegram-pending').first().locator('summary').click();
  await page.locator('.telegram-pending').first().getByText('Đăng ngay sau khi bạn duyệt',{exact:true}).waitFor();
  assert.equal(await page.locator('.telegram-pending').first().getByText('Lịch:',{exact:false}).count(),0,'Immediate approval never labels the old due anchor as a future post schedule');
  assert.equal(writes.length,beforeDates,'Showing immediate mode never sends or approves');
  snapshot={...snapshot,reminders:{interval_seconds:300,pending_targets:1,next_reminder_at:instant,counts:{sending:0,sent:2,uncertain:1}},pending:{...snapshot.pending,items:snapshot.pending.items.map(item=>({...item,approve_by:null,approval_policy:'remind_until_decision_v1',reminder_interval_seconds:300,binding:{...item.binding,approval_policy:'remind_until_decision_v1'}}))}};
  await page.evaluate(value=>panel.adopt(value),snapshot);await page.locator('.telegram-pending').first().locator('summary').click();
  await page.getByRole('region',{name:'Nhắc duyệt tự động',exact:true}).getByText('Nhắc duyệt tự động · 5 phút',{exact:true}).waitFor();
  await page.locator('.telegram-pending').first().getByText('Sau giờ đăng, nhắc duyệt mỗi 5 phút đến khi bạn duyệt hoặc từ chối.',{exact:true}).waitFor();
  assert.equal(await page.locator('.telegram-pending').first().getByText(/^Hạn duyệt:/).count(),0,'Continuous review has no misleading expiry label');
  assert.equal(writes.length,beforeDates,'Reminder policy display never sends a message or grants approval');
  const beforeBulk=writes.length;const privateValue='RAW_PRIVATE_FIELD_MUST_NOT_DISPLAY';
  snapshot={...snapshot,batches:{total:4,items:[{batch_id:'12345678-1234-4123-8123-123456789abe',state:'sent',total:2,approve_by:instant},{batch_id:'b'.repeat(64),state:'approved',total:20,approve_by:instant},{batch_id:privateValue,state:privateValue,total:2,approve_by:privateValue},{batch_id:'c'.repeat(64),state:'sent',total:21,approve_by:instant}]},notifications:{counts:{sent:2,pending:1,sending:1,uncertain:0,invalidated:3},total:7,items:[{raw_error:privateValue}]}};
  await page.evaluate(value=>panel.adopt(value),snapshot);
  const batches=page.getByRole('region',{name:'Nhóm duyệt Telegram',exact:true});await batches.getByText('Nhóm 12345678 · 2 video',{exact:true}).waitFor();
  await batches.getByText('Nhóm bbbbbbbb · 20 video',{exact:true}).waitFor();await batches.getByText('Đã ghi nhận duyệt nhóm',{exact:true}).waitFor();
  await batches.getByText('Trạng thái cần đối chiếu',{exact:true}).waitFor();assert.equal(await batches.locator('.telegram-batch-summary').count(),3,'Groups larger than 20 do not appear as an approvable summary');
  assert.equal(await batches.locator('button,input,a').count(),0,'Batch summary has no web approval or send action');
  assert.equal(await page.getByRole('region',{name:'Thông báo sau duyệt và đăng',exact:true}).locator('dd').allTextContents().then(values=>values.join(',')),'2,1,1,0,3');
  assert.ok(!(await page.locator('body').innerText()).includes(privateValue));assert.equal(writes.length,beforeBulk,'Adopting batch and status snapshots never sends or approves');
  snapshot={...snapshot,enabled:false};await page.evaluate(value=>panel.adopt(value),snapshot);
  await page.getByText('Theo dõi video đã duyệt',{exact:true}).waitFor();
  await page.getByText('Tự gửi video mới đang tắt;',{exact:false}).waitFor();
  assert.equal(await page.getByLabel('Bật nhận duyệt Telegram',{exact:true}).isChecked(),false);
  assert.equal(writes.length,beforeBulk,'Following status does not enable global delivery');
  await page.locator('.telegram-pending').first().locator('summary').click();
  await page.getByRole('button',{name:'Gửi thử Telegram cho Page 555',exact:true}).click();
  await page.getByText('Đang thử 1 video',{exact:true}).waitFor();
  await page.getByText('Đã gửi video thử và nút duyệt.',{exact:false}).waitFor();
  assert.equal(writes.length,beforeDates+1);assert.equal(writes.at(-1).path,'/api/telegram-approval/trial');
  assert.equal(snapshot.enabled,false,'A single trial never enables global notifications');
  assert.equal(await page.locator('.telegram-pending button:enabled').count(),0,'Cannot send a second pending video during the trial');
  const beforeFollowing=writes.length;
  snapshot={...snapshot,trial:{...snapshot.trial,state:'decided',active:false},batches:{items:[],total:0},notifications:{counts:{pending:0,sending:0,sent:0,uncertain:0,invalidated:0},items:[],total:0}};
  await page.evaluate(value=>panel.adopt(value),snapshot);await page.getByText('Theo dõi video đã duyệt',{exact:true}).waitFor();
  assert.equal(await page.getByLabel('Bật nhận duyệt Telegram',{exact:true}).isChecked(),false);assert.equal(writes.length,beforeFollowing);
  snapshot={...snapshot,trial:null,notifications:{counts:{pending:0,sending:0,sent:0,uncertain:0,invalidated:0},items:[],total:0}};
  await page.evaluate(value=>panel.adopt(value),snapshot);await page.getByText('Chưa bật bot',{exact:true}).waitFor();
  assert.equal(await page.getByText('Theo dõi video đã duyệt',{exact:true}).count(),0,'No follow-up claim without review history');
  const beforeCohort=writes.length;
  snapshot={...snapshot,sending_notifications:1,uncertain_notifications:0,cohort:{cohort_id:'12345678-1234-4123-8123-123456789abf',state:'active',active:true,total:14,expires_at:instant,counts:{pending:12,approved:1,rejected:1,delivered:13,sending:1,uncertain:0}}};
  await page.evaluate(value=>panel.adopt(value),snapshot);
  const cohort=page.getByRole('region',{name:'Phiên duyệt Telegram riêng',exact:true});
  await cohort.getByRole('heading',{name:'Phiên duyệt riêng · 14 video',exact:true}).waitFor();
  assert.equal(await cohort.locator('dd').allTextContents().then(values=>values.join(',')),'13,1,0,12,1,1');
  assert.equal(await page.locator('.telegram-approval dl').first().locator('dd').allTextContents().then(values=>values.slice(-2).join(',')),'1,0','In-flight sends are separate from ambiguous outcomes');
  assert.equal(await page.getByLabel('Bật nhận duyệt Telegram',{exact:true}).isChecked(),false);
  assert.equal(await cohort.locator('button,input,a').count(),0);
  assert.equal(await page.locator('.telegram-pending button:enabled').count(),0,'An active cohort cannot start an unrelated single trial');
  assert.equal(writes.length,beforeCohort,'Scope metadata never sends or approves from the web');
  await page.setViewportSize({width:390,height:844});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,'Full dates and visible IANA zones fit mobile');
  assert.equal(await page.evaluate(()=>localStorage.length+sessionStorage.length),0);
  assert.ok(!(await page.locator('body').innerText()).includes(secret));assert.deepEqual(outside,[]);assert.deepEqual(errors,[]);
  await page.screenshot({path:path.resolve(__dirname,'../.runtime/telegram-approval-ui.png'),fullPage:true});
  await context.close();console.log('Telegram approval UI offline PASS');
 }finally{await browser.close()}
})().catch(error=>{console.error(error);process.exitCode=1});
