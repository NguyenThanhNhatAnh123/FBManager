// Offline synthetic folder-status fixture only. No real files, tokens or jobs.
const {chromium}=require('../../chrome-manager/frontend/node_modules/playwright');
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const root=path.resolve(__dirname,'..'),frontend=path.join(root,'frontend'),base=process.argv.includes('--mounted')?'/pages':'',origin=base?'http://127.0.0.1:8000':'http://127.0.0.1:8010';
const DIRECTORY='C:\\Users\\Admin\\Video\\01-11-2026';
(async()=>{
 const browser=await chromium.launch({channel:'chrome',headless:true});
 try{
  const context=await browser.newContext({offline:true,viewport:{width:1500,height:1050}}),page=await context.newPage(),writes=[],reads=[],outside=[],errors=[];
  let sessions=0,putMode='',scanMode='',getMode='',catalogMode='',release=null,legacyStatus=false;
  const csrf=()=>`fixture-source-${sessions}`,edge=id=>({id,label:id===1?'Nhật · Chính':'Nguồn phụ',status:'working',has_page_token:true});
  const catalog=Array.from({length:14},(_,i)=>({id:String(90000001+i),name:'Page '+String(i+1).padStart(2,'0'),can_publish:true,information_status:'updated',status:'active',category:'Fixture',connections:i===1?[edge(1),edge(2)]:[edge(1)],details:{},posts_today:null,posts_today_complete:false}));
  catalog.push({id:'90000099',name:'Trang mới chưa sửa',can_publish:false,information_status:'not_updated',status:'active',connections:[edge(1)],details:{},posts_today:null,posts_today_complete:false});
  let status={configured:false,settings:{enabled:false,directory:DIRECTORY,page_ids:[],connection_id:1,daily_time:'06:00',timezone:'America/Los_Angeles',caption_template:'Funny tees. Big laughs. Wear your sense of humor.\nShop more laughs: https://blueox179.com\n#FunnyTees #TShirtQuotes #WearYourHumor'},stats:{files_seen:0,waiting:0,imported:0,queued:0,skipped:0,errors:0},items:[],running:false,next_page_id:null,last_scan_at:null,last_error:null};
  const clone=value=>JSON.parse(JSON.stringify(value));
  const pageStatus=(limit=100,offset=0)=>legacyStatus?clone(status):{...clone(status),items:clone(status.items.slice(offset,offset+limit)),total:status.items.length,limit,offset};
  await page.addInitScript(()=>{const original=window.setInterval;window.setInterval=(fn,delay,...args)=>{if(delay===5000){window.fixtureSourcePoll=fn;return original(()=>{},delay)}return original(fn,delay,...args)}});
  await page.route('**/*',async route=>{
   const req=route.request(),url=new URL(req.url());
   if(url.origin===origin&&url.pathname==='/auth/api/me')return route.fulfill({json:{user:{id:1,username:'fixture',role:'admin',must_change_password:false},csrf_token:'fixture-via-csrf'}});
   if(url.origin===origin&&/^(?:\/pages)?\/api\//.test(url.pathname)&&!['GET','HEAD','OPTIONS'].includes(req.method()))assert.equal(req.headers()['x-via-csrf'],'fixture-via-csrf');

   if(url.origin!==origin||!url.pathname.startsWith(base+'/')){outside.push(req.url());return route.abort()}
   const p=url.pathname.slice(base.length),method=req.method(),body=req.postData()?req.postDataJSON():null;
   if(p.startsWith('/api/')){
    if(method==='GET')reads.push({path:p,query:url.search});else{writes.push({path:p,method,body});assert.equal(req.headers()['x-mpm-csrf'],csrf())}
    if(p==='/api/session'){sessions++;return route.fulfill({json:{csrf_token:csrf(),app:{name:'Page Manager',timezone:'UTC',video_source:true,token_reels:false}}})}
    if(p==='/api/connections')return route.fulfill({json:{items:[1,2].map(id=>({...edge(id),connected:true,has_token:true,token_type:'system_user'}))}});
    if(p==='/api/overview')return route.fulfill({json:{stats:{pages:14},updated_at:new Date().toISOString()}});
    if(p==='/api/pages'){if(catalogMode==='503-once'){catalogMode='';return route.fulfill({status:503,json:{detail:'Page catalog unavailable fixture'}})}const rows=(url.searchParams.get('connection_id')==='2'?[catalog[1]]:catalog).filter(row=>(row.name+' '+row.id).toLowerCase().includes((url.searchParams.get('search')||'').toLowerCase())),limit=Number(url.searchParams.get('limit')||100),offset=Number(url.searchParams.get('offset')||0);return route.fulfill({json:{items:rows.slice(offset,offset+limit),total:rows.length,limit,offset}})}
    if(p==='/api/connections/options')return route.fulfill({json:{items:[1,2].map(id=>({...edge(id),connected:true,has_token:true})),total:2,limit:100,offset:0}});
    if(p==='/api/events')return route.fulfill({json:{items:[]}});
    if(p==='/api/video-source'){
     if(getMode==='404'){getMode='';return route.fulfill({status:404,json:{detail:'Feature missing in fixture'}})}
     if(getMode==='503'){getMode='';return route.fulfill({status:503,json:{detail:'Source temporarily unavailable'}})}
     const value=pageStatus(Number(url.searchParams.get('limit')||100),Number(url.searchParams.get('offset')||0));
     if(getMode==='delay'){getMode='';await new Promise(resolve=>{release=resolve})}
     return route.fulfill({json:value}).catch(()=>{});
    }
    if(p==='/api/video-source/settings'){
     assert.equal(method,'PUT');assert.deepEqual(Object.keys(body).sort(),['caption_template','connection_id','daily_time','enabled','page_ids','timezone',...(Object.hasOwn(body,'revision')?['revision']:[])].sort());
     if(putMode==='403'){putMode='';return route.fulfill({status:403,json:{detail:'Fixture session expired'}})}
     if(Number.isInteger(status.settings.revision)&&body.revision!==status.settings.revision)return route.fulfill({status:409,json:{detail:'Cấu hình đã thay đổi. Nạp lại cấu hình đã lưu trước khi lưu tiếp.'}});
     status={...status,configured:true,settings:{...body,directory:DIRECTORY,...(Number.isInteger(status.settings.revision)?{revision:status.settings.revision+1}:{})}};
     return route.fulfill({json:pageStatus()});
    }
    if(p==='/api/video-source/scan'){
     assert.equal(method,'POST');assert.deepEqual(body,{});
     status={...status,stats:{files_seen:1,waiting:0,imported:1,queued:1,skipped:0,errors:0},next_page_id:catalog[1].id,last_scan_at:new Date().toISOString(),items:[{filename:'<script>window.bad=1</script>.mp4',status:'queued',page_id:catalog[0].id,page_name:catalog[0].name,scheduled_local:'2030-11-02T06:00:00',timezone:'America/Los_Angeles',job_id:77,message:'Fixture queued once'}]};
     if(scanMode==='network'){scanMode='';return route.abort('failed')}
     return route.fulfill({json:pageStatus()});
    }
    return route.fulfill({status:404,json:{detail:'Unknown folder fixture API'}});
   }
   if(['/','/schedule','/saved','/settings'].includes(p))return route.fulfill({path:path.join(frontend,'index.html'),contentType:'text/html'});
   if(p.startsWith('/static/')){const file=path.resolve(frontend,p.slice(8));if(!file.startsWith(frontend+path.sep)||!fs.existsSync(file))return route.abort();return route.fulfill({path:file,contentType:file.endsWith('.js')?'application/javascript':file.endsWith('.woff2')?'font/woff2':'text/css'})}
   return route.abort();
  });
  page.on('pageerror',err=>errors.push(err.message));await page.goto(origin+base+'/');
  await page.getByRole('combobox',{name:'Lọc nguồn token',exact:true}).selectOption('2');
  await page.getByRole('button',{name:'Settings',exact:true}).click();await page.getByRole('tab',{name:'Nguồn video',exact:true}).click();
  const area=page.getByRole('tabpanel',{name:'Nguồn video',exact:true});
  const enable=()=>area.getByRole('checkbox',{name:'Bật nhập video tự động',exact:true}),caption=()=>area.getByRole('textbox',{name:'Caption mặc định cho video',exact:true}),save=()=>area.getByRole('button',{name:'Lưu cấu hình nguồn video',exact:true}),scan=()=>area.getByRole('button',{name:'Quét & xếp lịch ngay',exact:true});
  await area.locator('.video-source-config > summary').click();
  await caption().waitFor();assert.equal(await caption().getAttribute('maxlength'),'2200');assert.equal(await area.getByRole('textbox',{name:'Thư mục nguồn video',exact:true}).inputValue(),DIRECTORY);assert.equal(await area.getByRole('textbox',{name:'Thư mục nguồn video',exact:true}).getAttribute('readonly'),'true');
  assert.equal(await area.getByRole('checkbox',{name:/Chia video cho Page/}).count(),15,'Source chooser shows blocked Pages with their reason');assert.equal(await area.getByRole('checkbox',{name:/Chia video cho Page/,checked:true}).count(),14,'Unconfigured defaults select only eligible Pages');
  assert.equal(await area.getByRole('checkbox',{name:'Chia video cho Page Trang mới chưa sửa',exact:true}).isDisabled(),true);
  assert.equal(await area.getByRole('button',{name:'Chỉnh sửa Trang mới chưa sửa',exact:true}).isEnabled(),true);
  await area.getByRole('button',{name:'Chọn tất cả Page của nguồn',exact:true}).click();assert.equal(await area.getByRole('checkbox',{name:'Chia video cho Page Trang mới chưa sửa',exact:true}).isChecked(),false,'Select All never enables an unedited Page');
  assert.equal(await area.getByRole('textbox',{name:'Giờ đăng mỗi ngày',exact:true}).inputValue(),'06:00');assert.equal(await area.getByRole('textbox',{name:'Múi giờ nguồn video',exact:true}).inputValue(),'America/Los_Angeles');assert.ok((await caption().inputValue()).includes('https://blueox179.com'));
  await area.getByRole('heading',{name:'Thư mục chưa có video',exact:true}).waitFor();assert.equal(await scan().isDisabled(),true);assert.equal(writes.length,0,'Opening/rendering/polling never scans or saves');
  await enable().check();await caption().fill('Draft caption with https://blueox179.com');
  status.settings.caption_template='External saved caption';await page.evaluate(()=>window.fixtureSourcePoll());await page.waitForTimeout(60);assert.equal(await caption().inputValue(),'Draft caption with https://blueox179.com','Polling keeps staged caption');assert.equal(await enable().isChecked(),true);assert.equal(writes.length,0);
  await save().evaluate(node=>{node.click();node.click()});await area.getByText('Đã lưu và bật nhập tự động. Video mới sẽ được xếp lịch theo tiêu chí đã chọn.',{exact:true}).waitFor();
  assert.equal(writes.length,1);assert.equal(writes[0].body.page_ids.length,14);assert.equal(writes[0].body.connection_id,1);assert.equal(writes[0].body.daily_time,'06:00');assert.equal(writes[0].body.timezone,'America/Los_Angeles');assert.equal(await scan().isDisabled(),false);
  await scan().click();await area.locator('.video-source-item-detail > summary').first().click();await area.getByRole('button',{name:'Lịch #77',exact:true}).waitFor();assert.equal(writes.length,2);assert.equal(await page.evaluate(()=>window.bad),undefined);await area.getByText('<script>window.bad=1</script>.mp4',{exact:true}).waitFor();
  const queueLabel='Hàng đợi nguồn video',pager=()=>area.getByRole('navigation',{name:'Phân trang '+queueLabel,exact:true}),queueRows=()=>area.locator('.video-source-table tbody tr');
  const originalItem=status.items[0],beforePagingWrites=writes.length;status.items=[originalItem,...Array.from({length:122},(_,i)=>({...originalItem,filename:'Video nguồn '+String(i+2).padStart(3,'0')+'.mp4',job_id:100+i}))];status.stats.files_seen=status.items.length;
  await caption().fill('Draft remains while browsing source queue');await page.evaluate(()=>window.fixtureSourcePoll());await pager().waitFor();assert.equal(await queueRows().count(),10,'Queue renders a bounded page, not all source records');
  await pager().getByRole('button',{name:'Trang sau · '+queueLabel,exact:true}).click();await area.getByText('Video nguồn 011.mp4',{exact:true}).waitFor();assert.equal(await queueRows().count(),10);
  await page.evaluate(()=>window.fixtureSourcePoll());await page.waitForTimeout(60);await area.getByText('Video nguồn 011.mp4',{exact:true}).waitFor();assert.equal(await caption().inputValue(),'Draft remains while browsing source queue','Queue pagination and polling preserve the form draft');
  await pager().getByRole('button',{name:'Trang cuối · '+queueLabel,exact:true}).click();await area.getByText('Video nguồn 123.mp4',{exact:true}).waitFor();assert.equal(await queueRows().count(),3,'Rows beyond the old 100-record cap remain reachable');
  await pager().getByRole('combobox',{name:'Số mục mỗi trang · '+queueLabel,exact:true}).selectOption('25');await area.getByRole('button',{name:'Lịch #77',exact:true}).waitFor();assert.equal(await queueRows().count(),25,'Page-size change resets to the first page');
  await pager().getByRole('button',{name:'Trang cuối · '+queueLabel,exact:true}).click();await area.getByText('Video nguồn 123.mp4',{exact:true}).waitFor();status.items=[originalItem];await page.evaluate(()=>window.fixtureSourcePoll());await area.getByRole('button',{name:'Lịch #77',exact:true}).waitFor();assert.equal(await queueRows().count(),1);assert.equal(await pager().getByRole('button',{name:'Trang sau · '+queueLabel,exact:true}).isDisabled(),true,'Polling clamps a page whose records were removed');assert.equal(writes.length,beforePagingWrites,'Paging and size changes never save, scan or create schedules');
  assert.ok(reads.some(row=>row.path==='/api/video-source'&&new URLSearchParams(row.query).get('offset')==='120'),'Queue requests the server page beyond its original 100-row window');
  legacyStatus=true;await page.evaluate(()=>window.fixtureSourcePoll());await area.getByText('Đang hiển thị danh sách video gần nhất do máy chủ cung cấp; chưa xác định tổng số.',{exact:true}).waitFor();legacyStatus=false;await page.evaluate(()=>window.fixtureSourcePoll());await page.waitForTimeout(60);assert.equal(await area.getByText(/Đang hiển thị danh sách video gần nhất/).count(),0,'A restarted server upgrades the list from recent-only fallback to true pagination');
  catalog.push(...Array.from({length:61},(_,index)=>({...catalog[0],id:String(90000015+index),name:'Page '+String(index+15).padStart(2,'0')})));
  const choices=()=>area.getByRole('navigation',{name:'Phân trang Page nhận video',exact:true});await area.getByRole('searchbox',{name:'Tìm Page nhận video',exact:true}).fill('Page');await area.getByRole('checkbox',{name:'Chia video cho Page Page 25',exact:true}).waitFor();assert.equal(await area.getByRole('checkbox',{name:/Chia video cho Page/}).count(),25,'Page chooser loads a bounded SQL window');
  await choices().getByRole('button',{name:'Trang cuối · Page nhận video',exact:true}).click();await area.getByRole('checkbox',{name:'Chia video cho Page Page 75',exact:true}).check();await choices().getByRole('button',{name:'Trang đầu · Page nhận video',exact:true}).click();await area.getByRole('checkbox',{name:'Chia video cho Page Page 01',exact:true}).waitFor();assert.equal(await area.getByRole('checkbox',{name:'Chia video cho Page Page 01',exact:true}).isChecked(),true,'Selections persist outside the current Page chooser window');assert.equal(await caption().inputValue(),'Draft remains while browsing source queue');assert.equal(writes.length,beforePagingWrites,'Source options paging only reads cached server data');assert.ok(reads.some(row=>row.path==='/api/pages'&&new URLSearchParams(row.query).get('offset')==='50'));
  await enable().uncheck();await save().click();await area.getByText('Đã lưu và tạm dừng nhập mới. Những lịch đã tạo vẫn tiếp tục chạy.',{exact:true}).waitFor();assert.equal(status.items[0].job_id,77);assert.equal(await scan().isDisabled(),true);
  await caption().fill('Survive navigation');await page.getByRole('button',{name:'Pages',exact:true}).click();const beforeUnmount=reads.filter(row=>row.path==='/api/video-source').length;await page.evaluate(()=>window.fixtureSourcePoll());await page.waitForTimeout(40);assert.equal(reads.filter(row=>row.path==='/api/video-source').length,beforeUnmount,'Unmounted component stops status polling');
  await page.getByRole('button',{name:'Settings',exact:true}).click();await caption().waitFor();assert.equal(await caption().inputValue(),'Survive navigation');
  await area.getByRole('button',{name:'Nạp lại cấu hình đã lưu',exact:true}).click();assert.equal(await caption().inputValue(),status.settings.caption_template);
  putMode='403';await caption().fill('Explicit retry only');const before403=writes.length;await save().click();await area.getByText(/Phiên làm việc đã được làm mới/).first().waitFor();assert.equal(writes.length,before403+1,'CSRF refresh never replays PUT');assert.equal(sessions,2);
  await save().click();await area.getByText('Đã lưu và tạm dừng nhập mới. Những lịch đã tạo vẫn tiếp tục chạy.',{exact:true}).waitFor();
  await enable().check();await save().click();await area.getByText('Đã lưu và bật nhập tự động. Video mới sẽ được xếp lịch theo tiêu chí đã chọn.',{exact:true}).waitFor();
  scanMode='network';const beforeNetwork=writes.length;await scan().click();await area.getByText(/Chưa xác định đầy đủ kết quả quét/).first().waitFor();await page.waitForTimeout(80);assert.equal(writes.length,beforeNetwork+1,'Interrupted scan does not auto-repeat');await area.getByRole('button',{name:'Đọc lại trạng thái',exact:true}).click();await page.waitForTimeout(60);assert.equal(writes.length,beforeNetwork+1);
  getMode='503';await page.evaluate(()=>window.fixtureSourcePoll());await area.getByText('Source temporarily unavailable',{exact:true}).waitFor();await page.evaluate(()=>window.fixtureSourcePoll());await page.waitForTimeout(60);assert.equal(await area.getByText('Source temporarily unavailable',{exact:true}).count(),0,'Status error clears after successful read');
  assert.ok(writes.filter(item=>item.method==='PUT').every(item=>!Object.hasOwn(item.body,'revision')),'Legacy status omits revision without changing the six-field request');
  status.settings.revision=4;await area.getByRole('button',{name:'Đọc lại trạng thái',exact:true}).click();await page.waitForTimeout(50);await area.getByRole('button',{name:'Nạp lại cấu hình đã lưu',exact:true}).click();
  await caption().fill('Keep my conflicting draft');status.settings.revision=5;status.settings.caption_template='New criteria saved by Codex';await page.evaluate(()=>window.fixtureSourcePoll());await page.waitForTimeout(60);
  const beforeConflict=writes.length;await save().click();await area.getByText(/Cấu hình đã thay đổi\. Nạp lại cấu hình đã lưu/).first().waitFor();
  assert.equal(writes.length,beforeConflict+1);assert.equal(writes.at(-1).body.revision,4,'Polling never upgrades the revision of a staged web draft');assert.equal(await caption().inputValue(),'Keep my conflicting draft','Conflict preserves unsaved content');
  await page.waitForTimeout(80);assert.equal(writes.length,beforeConflict+1,'409 never automatically saves again');
  await area.getByRole('button',{name:'Nạp lại cấu hình đã lưu',exact:true}).click();assert.equal(await caption().inputValue(),'New criteria saved by Codex','Explicit reload adopts the latest stored criteria');
  status.settings={...status.settings,page_ids:['90000099'],connection_id:999,enabled:true};status.posting_blocked_page_ids=['90000099'];
  await area.getByRole('button',{name:'Đọc lại trạng thái',exact:true}).click();await page.waitForTimeout(50);await area.getByRole('button',{name:'Nạp lại cấu hình đã lưu',exact:true}).click();
  await area.getByRole('searchbox',{name:'Tìm Page nhận video',exact:true}).fill('');await area.getByRole('checkbox',{name:'Chia video cho Page Trang mới chưa sửa',exact:true}).waitFor();
  assert.equal(await area.getByRole('checkbox',{name:'Chia video cho Page Trang mới chưa sửa',exact:true}).isChecked(),false,'An existing blocked selection is never shown enabled');
  catalogMode='503-once';await area.getByRole('searchbox',{name:'Tìm Page nhận video',exact:true}).fill('unavailable');await area.getByText('Page catalog unavailable fixture',{exact:true}).first().waitFor();
  assert.equal(await save().isDisabled(),true,'Catalog failure still prevents changing or enabling a source');
  await enable().uncheck();await save().click();await area.getByText('Đã lưu và tạm dừng nhập mới. Những lịch đã tạo vẫn tiếp tục chạy.',{exact:true}).waitFor();
  assert.deepEqual(writes.at(-1).body.page_ids,['90000099'],'Pausing preserves the saved pool even when its filtered draft is empty');assert.equal(writes.at(-1).body.connection_id,999,'Pausing remains possible after source access is unavailable');assert.equal(writes.at(-1).body.enabled,false);
  await area.getByRole('searchbox',{name:'Tìm Page nhận video',exact:true}).fill('');await area.getByRole('checkbox',{name:'Chia video cho Page Trang mới chưa sửa',exact:true}).waitFor();
  status.settings.connection_id=1;
  status={...status,configured:true,settings:{...status.settings,page_ids:[],enabled:false}};await area.getByRole('button',{name:'Đọc lại trạng thái',exact:true}).click();await page.waitForTimeout(50);await area.getByRole('button',{name:'Nạp lại cấu hình đã lưu',exact:true}).click();assert.equal(await area.getByRole('checkbox',{name:/Chia video cho Page/,checked:true}).count(),0,'Saved empty choice is not silently refilled');
  // A delayed old status cannot overwrite a fresh result after leaving/returning.
  status.last_error='OLD_STALE_RESULT';getMode='delay';await page.evaluate(()=>window.fixtureSourcePoll());for(let i=0;i<50&&!release;i++)await page.waitForTimeout(20);assert.ok(release);
  await page.getByRole('button',{name:'Pages',exact:true}).click();status.last_error='NEW_CURRENT_RESULT';await page.getByRole('button',{name:'Settings',exact:true}).click();await area.getByText('NEW_CURRENT_RESULT',{exact:true}).waitFor();release();release=null;await page.waitForTimeout(60);assert.equal(await area.getByText('OLD_STALE_RESULT',{exact:true}).count(),0);
  await area.locator('.video-source-config > summary').click();
  await page.setViewportSize({width:390,height:844});const overflow=await page.evaluate(()=>({bad:document.documentElement.scrollWidth>innerWidth,elements:['html','body','.main','#view-content','.workspace-settings','.settings-tabs','.settings-section','.video-source','.video-source-status','.video-source-table'].map(sel=>{const node=document.querySelector(sel),box=node.getBoundingClientRect();return {sel,width:box.width,right:box.right,scroll:node.scrollWidth,overflow:getComputedStyle(node).overflowX}})}));assert.equal(overflow.bad,false,'Mobile source/settings content stays contained: '+JSON.stringify(overflow.elements));
  assert.ok((await area.locator('.video-source-summary>div').first().boundingBox()).width>170,'Mobile summary retains a readable column');
  const output=path.join(root,'.runtime/ui');fs.mkdirSync(output,{recursive:true});await page.screenshot({path:path.join(output,(base?'mounted-':'')+'video-source-mobile.png'),fullPage:true});await page.setViewportSize({width:1500,height:1050});await page.screenshot({path:path.join(output,(base?'mounted-':'')+'video-source-settings.png'),fullPage:true});
  await area.getByRole('button',{name:'Xem lịch đăng Page',exact:true}).click();await page.getByRole('heading',{name:'Lịch đăng Page',exact:true}).waitFor();
  // On an old server a new session cannot advertise configured/running automation.
  getMode='404';await page.reload();await page.getByRole('button',{name:'Settings',exact:true}).click();await page.getByRole('tab',{name:'Nguồn video',exact:true}).click();await page.getByText(/Nguồn video chưa sẵn sàng trên server/).waitFor();assert.equal(await page.getByRole('button',{name:'Lưu cấu hình nguồn video',exact:true}).count(),0);
  assert.equal(writes.some(item=>/\/reels|\/profiles|\/publish/.test(item.path)),false);assert.deepEqual(outside,[]);assert.deepEqual(errors,[]);assert.equal(await page.evaluate(()=>localStorage.length+sessionStorage.length),0);assert.ok(reads.filter(item=>item.path==='/api/pages').some(item=>!item.query.includes('connection_id=')));
  console.log('PASS: '+(base?'mounted':'standalone')+' Settings video source: unfiltered14Page defaults, exact bindings/cadence, empty/paused/source errors, frozen draft across poll/nav, explicit save/scan only, no mutation replay, stale-read/unmount guards, safe literal filenames and responsive layout. Offline fixtures only.');
 }finally{await browser.close()}
})().catch(error=>{console.error(error);process.exit(1)});
