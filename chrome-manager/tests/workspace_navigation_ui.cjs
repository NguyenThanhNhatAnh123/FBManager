const countIs=async(locator,value,message)=>{for(let n=0;n<100;n++){if(await locator.count()===value)return;await new Promise(resolve=>setTimeout(resolve,50))}const result=await locator.count();require('node:assert/strict').equal(result,value,message)};
// Fresh offline browser and synthetic API only. Never opens saved profiles or writes real data.
const {chromium}=require('../frontend/node_modules/playwright');
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const root=path.resolve(__dirname,'..'),dist=path.resolve(process.env.CM_UI_DIST||path.join(root,'frontend/dist')),output=path.join(root,'.runtime/ui');
(async()=>{
 const browser=await chromium.launch({channel:'chrome',headless:true});
 try{
  const context=await browser.newContext({offline:true,serviceWorkers:'block',viewport:{width:1536,height:1000}}),page=await context.newPage();
  const errors=[],writes=[],unexpected=[],reads=[];
  require('./workspace_fixture.cjs').installWorkspaceFixture(page);
  page.on('pageerror',error=>errors.push(error.message));
  const stamp=new Date().toISOString(),older=new Date(Date.now()-60000).toISOString(),zone='America/Los_Angeles';
  const day=new Intl.DateTimeFormat('en-CA',{timeZone:zone,year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date());
  const post={url:'https://www.facebook.com/reel/9100000000000555',date:stamp};
  const cached=(id,name,url,extra={})=>({id,name,url,status:'posted',checked_at:stamp,check_date:day,timezone:zone,posts_today:1,posts:[post],error_message:null,...extra});
  const proxy={id:3,host:'192.0.2.3',port:8080,protocol:'http',status:'Working',ip_address:'192.0.2.3',latency_ms:1,last_checked:null,error_message:null,has_credentials:false};
  const profile=(id,name,pages,extra={})=>({id,name,profile_path:'fixture/'+id,proxy_id:null,proxy:null,browser_status:'Stopped',browser_mode:null,browser_error:null,task:null,outlook:null,bm:null,
   facebook:{name:'Facebook '+name,url:'https://www.facebook.com/account-'+id,status:'complete',checked_at:stamp,check_date:day,timezone:zone,fresh:true,error_message:null,pages},...extra});
  const profiles=[
   profile(3,'Fixture Alpha',[cached(11,'Shared cached Page','https://www.facebook.com/profile.php?id=555'),cached(13,'Stale duplicate title','https://facebook.com/profile.php?id=555&ref=old',{checked_at:older,status:'not_seen',posts_today:0,posts:[]}),cached(12,'Alpha only Page','https://www.facebook.com/alpha-only',{status:'not_seen',posts_today:0,posts:[]})],{proxy_id:3,proxy}),
   profile(7,'Fixture Beta',[cached(22,'Legacy shared title','https://m.facebook.com/profile.php?id=555&ref=fixture',{checked_at:older}),cached(23,'Numeric alias title','https://www.facebook.com/555',{checked_at:new Date(Date.now()-120000).toISOString()})]),
   profile(101,'Fixture BM',[],{bm:{portfolios:[],note:'Synthetic BM fixture',updated_at:stamp}}),
  ];
  const asset={id:'a'.repeat(32),name:'Saved fixture.mp4',size:2048,mime:'video/mp4',created_at:stamp,preview_url:'/api/reels/assets/'+'a'.repeat(32)+'/video'};
  const completed={id:2,profile_id:3,profile_name:'Fixture Alpha',page_url:'https://www.facebook.com/profile.php?id=555',page_name:'Shared cached Page',asset_id:asset.id,asset_name:asset.name,asset_size:asset.size,
   caption:'Synthetic completed Reel',scheduled_at:stamp,timezone:zone,status:'completed',step:'completed',message:'Fixture: completed after user confirmation.',created_at:stamp,updated_at:stamp,started_at:stamp,finished_at:stamp,
   published_url:'https://www.facebook.com/reel/900000000000002',verification_url:null,publish_attempted:true,can_verify_link:false,can_cancel:false,can_resume:false,can_archive:false,archived_at:null,can_restore:false};
  const completionSnapshot=JSON.stringify(completed);
  const system={cpu_percent:1,memory_percent:20,memory_used_mb:2048,memory_total_mb:16384,memory_available_mb:14336,total_profiles:3,running_browsers:0,queued_browsers:0,stopped_browsers:3,working_proxies:1,failed_proxies:0,total_proxies:1,running_tasks:0,pending_tasks:0,resource_warning:null,max_browsers:2,checking_proxies:0};
  const settings={max_browsers:2,max_memory_percent:85,max_cpu_percent:90,min_available_memory_mb:512,action_delay_ms:500,launch_timeout_seconds:30,task_timeout_seconds:300,require_proxy:false,proxy_ip_version:'auto',proxy_routing:'all',allowed_origins:['https://fixture.example'],facebook_auto_refresh:true};
  const history={items:[{id:8,task_id:4,profile_id:3,profile_name:'Fixture Alpha',status:'Completed',error_message:null,progress:100,created_at:stamp,started_at:stamp,finished_at:stamp,result:[]}],total:1};
  await page.routeWebSocket('**/ws',socket=>socket.close());
  await page.route('**/*',async route=>{
   const request=route.request(),url=new URL(request.url()),p=url.pathname;
   if(url.origin!=='http://127.0.0.1:8000'){unexpected.push(request.url());return route.abort()}
   if(p.startsWith('/api/')){
    if(request.method()!=='GET'){writes.push({method:request.method(),path:p});return route.fulfill({status:409,json:{detail:'Offline navigation fixture forbids writes'}})}
    reads.push(p);let data;
    if(p==='/api/session')data={};
    else if(p==='/api/profiles')data=profiles;
    else if(p==='/api/proxies')data=[proxy];
    else if(p==='/api/system/status')data=system;
    else if(p==='/api/tasks')data=[{id:4,name:'Fixture saved automation',status:'Idle',actions:['navigate'],runs:1,active_runs:0,created_at:stamp}];
    else if(p==='/api/tasks/history')data=history;
    else if(p==='/api/logs')data=[];
    else if(p==='/api/settings')data=settings;
    else if(p==='/api/facebook/refresh-status')data={enabled:true,interval_hours:24,running:false,eligible_count:1,due_count:0,next_due_at:new Date(Date.now()+86400000).toISOString(),last_finished_at:null,active_profile_id:null,profiles:[]};
    else if(p==='/api/page-recipient/config')data={asset_root:'fixture-assets'};
    else if(p==='/api/page-recipient/jobs')data=[];
    else if(p==='/api/reels/config')data={max_upload_bytes:1073741824,accepted_extensions:['.mp4','.m4v','.mov'],default_timezone:zone,scheduler_running:true};
    else if(p==='/api/reels/assets')data=[asset];
    else if(p===asset.preview_url)return route.fulfill({body:fs.readFileSync(path.join(__dirname,'fixtures/saved-schedule-preview.mp4')),contentType:'video/mp4'});
    else if(p==='/api/reels/pages')data=[{page_url:'https://www.facebook.com/profile.php?id=555',page_name:'Shared cached Page',source:'facebook_scan'}];
    else if(p==='/api/reels/page-eligibility')data={page_id:'555',posting_page_id:'555',can_publish:true,information_status:'updated',posting_block_reason:null,posting_revision:1};
    else if(p==='/api/reels')data=[completed];
    else {unexpected.push(p);return route.fulfill({status:404,json:{detail:'Unknown offline fixture route'}})}
    return route.fulfill({json:data});
   }
   const file=path.resolve(dist,'.'+(p==='/'||p.startsWith('/chrome/')?'/index.html':p));
   if(!file.startsWith(dist+path.sep)||!fs.existsSync(file)){unexpected.push(p);return route.abort()}
   return route.fulfill({path:file,contentType:file.endsWith('.js')?'application/javascript':file.endsWith('.css')?'text/css':file.endsWith('.woff2')?'font/woff2':'text/html'});
  });
  await page.goto('http://127.0.0.1:8000/');
  await page.getByRole('heading',{name:/^Chrome profiles/}).waitFor();
  await page.getByRole('checkbox',{name:'Select Fixture Alpha',exact:true}).waitFor();
  await countIs(page.locator('.profile-table tbody tr'),1,'Dashboard shows regular profiles with assigned proxies only');
  await countIs(page.getByRole('checkbox',{name:'Select Fixture Alpha',exact:true}),1);
  await countIs(page.getByRole('checkbox',{name:'Select Fixture Beta',exact:true}),0,'Unproxied cached profile stays outside Dashboard');
  const sidebar=page.locator('.sidebar'),nav=page.getByRole('navigation',{name:'Điều hướng chính',exact:true});
  assert.deepEqual(await nav.getByRole('button').allTextContents(),['Dashboard','Pages','Lịch đăng Page','Lịch đã lưu']);
  for(const name of ['Chrome Profiles','BM Profiles','Proxy Manager','Automation Tasks','Task History','Nhận Page & Setup'])await countIs(sidebar.getByRole('button',{name,exact:true}),0,name+' belongs in Settings');
  await countIs(sidebar.locator('.sidebar-bottom').getByRole('button',{name:'Settings',exact:true}),1);
  await page.getByRole('button',{name:'Collapse sidebar',exact:true}).click();
  await nav.getByRole('button',{name:'Pages',exact:true}).click();
  assert.equal(new URL(page.url()).pathname,'/chrome/pages');
  await page.getByRole('button',{name:'Expand sidebar',exact:true}).click();
  await page.locator('.chrome-pages-table tbody tr').first().waitFor();
  await countIs(page.locator('.chrome-pages-table tbody tr'),2,'Canonical Page ID merges host/query aliases and repeated source records');
  const shared=page.locator('.chrome-pages-table tbody tr').filter({has:page.getByRole('link',{name:'Shared cached Page',exact:true})});
  await countIs(shared,1);await countIs(shared.locator('.chrome-page-source'),2,'Same source profile appears once');
  assert.equal(await shared.getByRole('link',{name:'Shared cached Page',exact:true}).getAttribute('href'),'https://www.facebook.com/profile.php?id=555');
  await shared.getByText('Đã đăng · ≥1 bài',{exact:true}).waitFor();
  await shared.getByRole('button',{name:/Fixture Beta/}).click();
  const dialog=page.getByRole('dialog');await dialog.getByRole('heading',{name:'Fixture Beta · Facebook & Page',exact:true}).waitFor();
  await dialog.getByRole('button',{name:'Close dialog',exact:true}).click();
  await page.getByRole('textbox',{name:'Tìm Page hoặc profile nguồn',exact:true}).fill('Fixture Beta');
  await countIs(page.locator('.chrome-pages-table tbody tr'),1,'Source profile search keeps its canonical Page');
  await page.getByRole('textbox',{name:'Tìm Page hoặc profile nguồn',exact:true}).fill('');
  await page.getByRole('combobox',{name:'Trạng thái Page đã lưu',exact:true}).selectOption('not_seen');
  await page.getByRole('link',{name:'Alpha only Page',exact:true}).waitFor();
  await countIs(page.locator('.chrome-pages-table tbody tr'),1);
  await page.getByRole('combobox',{name:'Trạng thái Page đã lưu',exact:true}).selectOption('all');
  assert.equal(writes.length,0,'Cached catalog, filters and source dialogs never start Facebook or write data');
  await sidebar.getByRole('button',{name:'Settings',exact:true}).click();
  const sections=['BM Profiles','Proxy Manager','Automation Tasks','Task History','Nhận Page & Setup','Dữ liệu & công cụ','Cài đặt ứng dụng'];
  const tabs=page.getByRole('tablist',{name:'Nhóm Settings',exact:true});
  assert.deepEqual(await tabs.getByRole('tab').evaluateAll(elements=>elements.map(e=>e.getAttribute('aria-label'))),sections);
  for(const name of sections){
   await tabs.getByRole('tab',{name,exact:true}).click();
   await page.getByRole('tabpanel',{name,exact:true}).waitFor();
   assert.equal(await tabs.getByRole('tab',{name,exact:true}).getAttribute('aria-selected'),'true');
  }
  await page.getByRole('heading',{name:'Resource controls',exact:true}).waitFor();
  const autoRefresh=page.getByRole('checkbox',{name:'Tự động cập nhật tên Facebook và Page',exact:true});
  await page.waitForFunction(()=>{const input=document.querySelector('.facebook-refresh-settings input[type="checkbox"]');return input&&!input.disabled});
  assert.equal(await autoRefresh.isChecked(),true,'Valid refresh status exposes the saved Settings checkbox without writes');
  await tabs.getByRole('tab',{name:'BM Profiles',exact:true}).focus();
  await page.keyboard.press('ArrowDown');
  assert.equal(await tabs.getByRole('tab',{name:'Proxy Manager',exact:true}).getAttribute('aria-selected'),'true','Settings sections support keyboard navigation');
  await page.keyboard.press('End');
  assert.equal(await tabs.getByRole('tab',{name:'Cài đặt ứng dụng',exact:true}).getAttribute('aria-selected'),'true');
  await page.keyboard.press('Home');
  await nav.getByRole('button',{name:'Dashboard',exact:true}).click();
  await page.getByRole('checkbox',{name:'Select Fixture Alpha',exact:true}).check();
  await nav.getByRole('button',{name:'Lịch đăng Page',exact:true}).click();
  const form=page.locator('.reels-form-panel > form.reels-form');
  await page.waitForFunction(()=>document.querySelector('select[aria-label="Profile đăng Reel"]')?.value==='3');
  assert.equal(await form.getByRole('combobox',{name:'Profile đăng Reel',exact:true}).inputValue(),'3','Selected Chrome profile carries into the existing scheduler');
  await countIs(form.getByRole('option',{name:/Fixture BM/}),0);
  await countIs(page.getByRole('article',{name:'Lịch Reel #2',exact:true}),0,'Saved schedules are no longer duplicated inside the create page');
  await form.getByRole('textbox',{name:'Caption Reel',exact:true}).fill('Unsent schedule navigation fixture');
  await nav.getByRole('button',{name:'Lịch đã lưu',exact:true}).click();
  await page.getByRole('heading',{name:'Lịch đã lưu',exact:true}).waitFor();
  await countIs(form,0,'Saved schedules have their own full page');
  const job=page.getByRole('article',{name:'Lịch Reel #2',exact:true});await job.waitFor();
  assert.equal(await job.locator('video').getAttribute('src'),asset.preview_url.replace('/api/','/api/v1/'),'Legacy stored video URLs render through the versioned media route');
  await job.getByText('Shared cached Page',{exact:true}).waitFor();
  await job.getByText('Synthetic completed Reel',{exact:true}).waitFor();
  await job.getByText(/America\/Los_Angeles/).waitFor();
  await countIs(job.getByRole('link',{name:'Mở Reel đã đăng',exact:true}),0,'Details are absent from default cards');
  await job.locator('video').click({position:{x:20,y:20}});await countIs(page.getByRole('dialog'),0,'Video controls do not open details');
  await job.getByRole('button',{name:'Xem chi tiết lịch Reel #2',exact:true}).click();
  const savedDetail=page.getByRole('dialog');await savedDetail.waitFor();
  assert.equal(await savedDetail.getByRole('link',{name:'Mở Reel đã đăng',exact:true}).getAttribute('href'),completed.published_url);
  for(const action of ['Tiếp tục lịch Reel #2','Hủy lịch Reel #2','Xóa lịch Reel #2','Xác minh link Reel #2'])await countIs(savedDetail.getByRole('button',{name:action,exact:true}),0,'Completed attempted job has no '+action);
  await countIs(savedDetail.getByRole('textbox',{name:'Link Reel đã đăng',exact:true}),0);
  await savedDetail.getByRole('button',{name:'Close dialog',exact:true}).click();
  assert.equal(JSON.stringify(completed),completionSnapshot,'Navigation never changes completed job2 or its attempted latch');
  await page.goBack();
  await page.waitForURL('**/chrome/reels/new');
  assert.equal(await form.getByRole('textbox',{name:'Caption Reel',exact:true}).inputValue(),'Unsent schedule navigation fixture','The new sidebar page preserves the unsent form draft');
  await page.goForward();
  await page.waitForURL('**/chrome/reels/saved');
  assert.deepEqual(writes,[]);assert.deepEqual(unexpected,[]);assert.deepEqual(errors,[]);
  assert.ok(reads.includes('/api/settings')&&reads.includes('/api/facebook/refresh-status')&&reads.includes('/api/page-recipient/jobs')&&reads.includes('/api/reels'),'All relocated sections retain their cached read paths');
  const font=await page.evaluate(async()=>{await document.fonts.load('400 14px Geist','Tiếng Việt ă â ê ô ơ ư đ');await document.fonts.ready;return {family:getComputedStyle(document.body).fontFamily,loaded:document.fonts.check('400 14px Geist','Tiếng Việt')}});
  assert.match(font.family,/Geist/);assert.equal(font.loaded,true,'Bundled common font supports Vietnamese');
  fs.mkdirSync(output,{recursive:true});
  await page.screenshot({path:path.join(output,'workspace-navigation-schedule.png'),fullPage:true});
  await nav.getByRole('button',{name:'Pages',exact:true}).click();
  await page.screenshot({path:path.join(output,'workspace-navigation-pages.png'),fullPage:true});
  await sidebar.getByRole('button',{name:'Settings',exact:true}).click();
  await page.screenshot({path:path.join(output,'workspace-navigation-settings.png'),fullPage:true});
  await page.goto('http://127.0.0.1:8000/chrome/settings?tab=bm');
  await page.getByRole('tabpanel',{name:'BM Profiles',exact:true}).waitFor();
  assert.deepEqual(writes,[]);assert.equal(JSON.stringify(completed),completionSnapshot);
  console.log('PASS: offline saved-schedule sidebar directly below creation, distinct form/list with local video and preserved unsent draft, bottom Settings, admin navigation, unchanged completed job and bundled Vietnamese typography; zero API writes.');
 }finally{await browser.close()}
})().catch(error=>{console.error(error);process.exit(1)});
