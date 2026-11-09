// Entirely offline: synthetic plans and intercepted API calls, no worker/Meta/shop IO.
const {chromium}=require('../../chrome-manager/frontend/node_modules/playwright');
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const frontend=path.resolve(__dirname,'../frontend'),base=process.argv.includes('--mounted')?'/pages':'',origin=base?'http://127.0.0.1:8000':'http://127.0.0.1:8010';
const copy=value=>JSON.parse(JSON.stringify(value));
const allocations=Array.from({length:7},(_,i)=>({idea_id:'fixture-idea-'+i,position:i+1,variant_count:2}));
const initial=()=>({settings:{enabled:false,revision:1,timezone:'America/Los_Angeles',daily_time:'06:00',max_batches_per_day:1},
 preview:{day_key:'2030-01-01',candidate_count:7,capacity:14,total_videos:14,allocations:copy(allocations),deferred_count:0,blocker_codes:['auto_disabled']},active_batch:null,blockers:[],state:'disabled',checked_at:null,coalesce_seconds:60});
(async()=>{
 const browser=await chromium.launch({channel:'chrome',headless:true});
 try{
  const context=await browser.newContext({offline:true,serviceWorkers:'block',viewport:{width:1200,height:1000}}),page=await context.newPage();
  const writes=[],reads=[],outside=[],errors=[];let value=initial(),release=null,mode='unknown',sessions=0;
  await page.route('**/*',async route=>{
   const request=route.request(),url=new URL(request.url());
   if(url.origin===origin&&url.pathname==='/auth/api/me')return route.fulfill({json:{user:{id:1,username:'fixture',role:'admin',must_change_password:false},csrf_token:'fixture-via-csrf'}});
   if(url.origin===origin&&/^(?:\/pages)?\/api\//.test(url.pathname)&&!['GET','HEAD','OPTIONS'].includes(request.method()))assert.equal(request.headers()['x-via-csrf'],'fixture-via-csrf');

   if(url.origin!==origin||!url.pathname.startsWith(base+'/')){outside.push(url.href);return route.abort()}
   const p=url.pathname.slice(base.length);
   if(p==='/')return route.fulfill({contentType:'text/html',body:'<!doctype html><html><head><link rel="stylesheet" href="'+base+'/static/styles.css"><link rel="stylesheet" href="'+base+'/static/workspace-theme.css"><link rel="stylesheet" href="'+base+'/static/idea-pipeline.css"></head><body><main style="max-width:900px;padding:15px;margin:auto"></main><script type="module">import {createIdeaAutomation} from "'+base+'/static/idea-automation.js";window.panel=createIdeaAutomation();document.querySelector("main").append(panel.node)</script></body></html>'});
   if(p.startsWith('/static/')){
    const file=path.resolve(frontend,p.slice(8));assert.ok(file.startsWith(frontend+path.sep));assert.ok(fs.existsSync(file));
    return route.fulfill({path:file,contentType:file.endsWith('.js')?'application/javascript':file.endsWith('.woff2')?'font/woff2':'text/css'});
   }
   if(p==='/api/session'){sessions++;return route.fulfill({json:{csrf_token:'fixture-automation-'+sessions,app:{}}})}
   if(p==='/api/idea-pipeline/automation'){
    if(request.method()==='GET'){reads.push(p);return route.fulfill({json:copy(value)})}
    assert.equal(request.method(),'PUT');assert.equal(request.headers()['x-mpm-csrf'],'fixture-automation-'+sessions);
    const body=request.postDataJSON();writes.push(body);assert.deepEqual(Object.keys(body),['enabled','revision']);
    await new Promise(resolve=>{release=resolve});
    if(mode==='403')return route.fulfill({status:403,json:{detail:'Expired fixture'}});
    assert.equal(body.revision,value.settings.revision);value.settings.enabled=body.enabled;value.settings.revision++;
    value.state=body.enabled?'coalescing':'disabled';value.preview.blocker_codes=body.enabled?[]:['auto_disabled'];
    if(mode==='unknown')return route.abort('failed');
    return route.fulfill({json:copy(value)});
   }
   throw Error('Unexpected fixture request '+request.method()+' '+p);
  });
  page.on('pageerror',reason=>errors.push(reason.message));page.setDefaultTimeout(7000);
  await page.goto(origin+base+'/');await page.getByRole('heading',{name:'Tự động xử lý idea',exact:true}).waitFor();
  const toggle=()=>page.getByRole('checkbox',{name:'Tự động xử lý idea mới',exact:true}),save=()=>page.getByRole('button',{name:'Lưu chế độ tự động',exact:true});
  assert.equal(await toggle().isChecked(),false);assert.equal(await toggle().isDisabled(),true);
  assert.deepEqual(reads,[]);assert.deepEqual(writes,[],'Creating the panel never reads or enables automation');
  await page.evaluate(value=>panel.adopt(value),copy(value));
  await page.getByText('7 idea → 14 video',{exact:true}).waitFor();assert.equal(await toggle().isEnabled(),true);assert.equal(await save().isDisabled(),true);
  const preview=page.getByRole('region',{name:'Kế hoạch tự động dự kiến',exact:true});
  assert.equal(await preview.getByRole('cell',{name:'2',exact:true}).count(),7);await preview.getByText('14 Page',{exact:true}).waitFor();
  await page.getByText(/Chỉ dùng idea bạn thêm vào kho/).waitFor();
  for(let i=0;i<3;i++)await page.evaluate(value=>panel.adopt(value),copy(value));
  assert.deepEqual(reads,[],'Board snapshots do not cause another GET or poll');
  await toggle().check();await save().evaluate(node=>{node.click();node.click()});
  await page.getByRole('button',{name:'Đang lưu…',exact:true}).waitFor();assert.equal(await toggle().isDisabled(),true);
  for(let i=0;i<100&&!release;i++)await page.waitForTimeout(10);assert.ok(release);assert.deepEqual(writes,[{enabled:true,revision:1}]);release();release=null;
  await page.getByRole('alert').getByText(/Chưa rõ kết quả lưu/).waitFor();assert.equal(await save().isDisabled(),true);assert.equal(await toggle().isDisabled(),true);
  await page.evaluate(value=>panel.adopt(value),copy(value));await page.waitForTimeout(30);
  assert.equal(writes.length,1,'A board refresh never replays an uncertain PUT');
  await page.getByRole('button',{name:'Đọc lại để đối chiếu',exact:true}).click();await page.getByText(/Đã đọc lại chế độ đang lưu/).waitFor();
  assert.equal(await toggle().isChecked(),true);assert.equal(await save().isDisabled(),true);assert.equal(reads.length,1);assert.equal(writes.length,1);
  value.active_batch={id:'fixture-batch',day_key:'2030-01-01',status:'running',snapshot:{candidate_count:7,total_videos:14,deferred_count:0,source:{page_ids:Array.from({length:14},(_,i)=>String(i+1))}},items:copy(allocations).map(row=>({...row,status:'queued'}))};
  value.preview={candidate_count:1,capacity:14,total_videos:14,allocations:[{idea_id:'late-idea',position:1,variant_count:14}],deferred_count:0,blocker_codes:[]};value.state='running';
  await page.evaluate(value=>panel.adopt(value),copy(value));
  const frozen=page.getByRole('region',{name:'Kế hoạch tự động đã khóa',exact:true});
  await frozen.getByText('7 idea → 14 video',{exact:true}).waitFor();assert.equal(await frozen.getByRole('cell',{name:'2',exact:true}).count(),7);assert.equal(await frozen.getByRole('cell',{name:'14',exact:true}).count(),0,'A later preview cannot grow or repartition the frozen batch');
  value.blockers=['auto_batch_source_changed','consumer_unavailable','uploader_workspace_busy','uploader_workspace_unknown','production_pipeline_unavailable','PRIVATE_UNKNOWN_REASON'];await page.evaluate(value=>panel.adopt(value),copy(value));
  await frozen.getByText('Nguồn hoặc nhóm Page đã đổi; cần đối chiếu kế hoạch đã khóa.',{exact:true}).waitFor();await frozen.getByText('Bộ nhận và kiểm tra video chưa sẵn sàng.',{exact:true}).waitFor();await frozen.getByText('Tool upload đang có input, review hoặc batch chưa xong. Giữ dữ liệu hiện có và xử lý trước khi giao việc mới.',{exact:true}).waitFor();await frozen.getByText('Chưa đọc được trạng thái vùng làm việc tool upload; cần kiểm tra trước khi giao việc mới.',{exact:true}).waitFor();await frozen.getByText('Bộ điều phối từng video chưa sẵn sàng.',{exact:true}).waitFor();assert.equal(await page.getByText('PRIVATE_UNKNOWN_REASON',{exact:true}).count(),0,'Unknown blocker text is never echoed');value.blockers=[];
  mode='success';await toggle().uncheck();await save().click();for(let i=0;i<100&&!release;i++)await page.waitForTimeout(10);assert.ok(release);release();release=null;
  await page.getByText('Đã tắt giao việc tự động mới. Lượt đang chạy và lịch đã lưu vẫn tiếp tục.',{exact:true}).waitFor();
  assert.deepEqual(writes.at(-1),{enabled:false,revision:2});await frozen.getByText('7 idea → 14 video',{exact:true}).waitFor();
  // A concurrent revision change blocks a stale draft until an explicit read.
  await toggle().check();value.settings.revision=4;await page.evaluate(value=>panel.adopt(value),copy(value));
  await page.getByRole('alert').getByText(/Chế độ đã thay đổi ở nơi khác/).waitFor();assert.equal(await save().isDisabled(),true);
  await page.getByRole('button',{name:'Đọc lại để đối chiếu',exact:true}).click();await page.getByText(/Đã đọc lại chế độ đang lưu/).waitFor();assert.equal(await toggle().isChecked(),false);
  mode='403';await toggle().check();await save().click();for(let i=0;i<100&&!release;i++)await page.waitForTimeout(10);assert.ok(release);release();release=null;
  await page.getByRole('alert').getByText(/Phiên làm việc đã được làm mới/).waitFor();assert.equal(sessions,2);assert.equal(writes.length,3,'CSRF/session refresh does not replay the PUT');
  assert.equal(await save().isDisabled(),true);assert.equal(await page.evaluate(()=>localStorage.length+sessionStorage.length),0);
  await page.setViewportSize({width:390,height:844});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
  assert.deepEqual(outside,[]);assert.deepEqual(errors,[]);
  console.log('Idea automation UI '+(base||'standalone')+': opt-in, dynamic allocation, frozen batch, unknown PUT, revision conflict and CSRF non-replay passed.');
 }finally{await browser.close()}
})().catch(reason=>{console.error(reason);process.exitCode=1});
