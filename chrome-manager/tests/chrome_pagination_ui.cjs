// Offline fixtures only: never reads or changes real queues, profiles, or accounts.
const {chromium}=require('../frontend/node_modules/playwright');
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const countIs=async(locator,value,message)=>{for(let n=0;n<100;n++){if(await locator.count()===value)return;await new Promise(resolve=>setTimeout(resolve,50))}assert.equal(await locator.count(),value,message)};
const textMatches=async(locator,value,message)=>{for(let n=0;n<100;n++){if(value.test(await locator.innerText()))return;await new Promise(resolve=>setTimeout(resolve,50))}assert.match(await locator.innerText(),value,message)};
const root=path.resolve(__dirname,'..'),dist=path.join(root,'frontend/dist');
(async()=>{
 const browser=await chromium.launch({channel:'chrome',headless:true});
 try{
  const page=await browser.newPage({offline:true,serviceWorkers:'block',viewport:{width:1600,height:1050}}),errors=[],writes=[],unexpected=[];
  require('./workspace_fixture.cjs').installWorkspaceFixture(page);
  page.on('pageerror',error=>errors.push(error.message));
  const stamp=new Date().toISOString(),day=new Intl.DateTimeFormat('en-CA',{timeZone:'America/Los_Angeles'}).format(new Date());
  const cachedPages=Array.from({length:26},(_,i)=>({id:i+1,name:'Fixture Page '+String(i+1).padStart(3,'0'),url:'https://www.facebook.com/profile.php?id='+(900000000000000+i),status:i%2?'not_seen':'posted',checked_at:stamp,check_date:day,timezone:'America/Los_Angeles',posts_today:i%2?0:1,posts:i%2?[]:[{url:'https://www.facebook.com/reel/'+(800000000000000+i),date:stamp}],error_message:null}));
  const profiles=Array.from({length:26},(_,i)=>({id:i+1,name:'Fixture Profile '+String(i+1).padStart(3,'0'),profile_path:'fixture/'+i,proxy_id:i===0?1:null,proxy:i===0?{id:1,host:'192.0.2.1',port:80,protocol:'http',has_credentials:false,ip_address:null}:null,browser_status:'Stopped',browser_mode:null,browser_error:null,task:null,outlook:null,bm:null,facebook:{name:'Fixture Facebook '+(i+1),url:null,status:'complete',checked_at:stamp,check_date:day,timezone:'America/Los_Angeles',fresh:true,error_message:null,pages:i===0?cachedPages:[]}}));
  profiles[25].browser_status='Running';profiles[25].browser_mode='manual';
  const originalJobs=profiles.map((profile,i)=>({id:'fixture-recipient-'+profile.id,profile_id:profile.id,profile_name:profile.name,status:i%2?'completed':'waiting_code',step:i%2?'completed':'waiting_code',message:'Synthetic progress '+(i+1),page_id:String(900000000000000+i),page_name:'Fixture Target '+String(i+1).padStart(3,'0'),started_at:stamp,updated_at:new Date(Date.now()-i*1000).toISOString(),code_deadline:i%2?null:new Date(Date.now()+300000).toISOString(),fields:{},active:i%2===0}));
  let jobs=[...originalJobs];
  const logs=Array.from({length:55},(_,i)=>({id:55-i,profile_id:1,level:i%2?'info':'error',message:'Synthetic activity '+String(i+1).padStart(3,'0'),created_at:stamp}));
  const system={cpu_percent:1,memory_percent:20,memory_used_mb:1024,memory_total_mb:8192,memory_available_mb:7168,total_profiles:26,running_browsers:0,queued_browsers:0,stopped_browsers:26,working_proxies:1,failed_proxies:0,total_proxies:1,running_tasks:0,pending_tasks:0,resource_warning:null,max_browsers:2,checking_proxies:0};
  await page.routeWebSocket('**/ws',socket=>socket.close());
  await page.route('**/*',async route=>{
   const request=route.request(),url=new URL(request.url()),p=url.pathname;
   if(url.origin!=='http://127.0.0.1:8000'){unexpected.push(request.url());return route.abort()}
   if(p.startsWith('/api/')){
    if(request.method()!=='GET'){writes.push({path:p,method:request.method()});return route.fulfill({status:409,json:{detail:'Offline pagination fixture forbids mutations'}})}
    let data;
    if(p==='/api/session')data={};
    else if(p==='/api/profiles')data=profiles;
    else if(p==='/api/system/status')data=system;
    else if(p==='/api/tasks/history')data={items:[],total:0};
    else if(p==='/api/tasks')data=[{id:4,name:'Synthetic automation',status:'Idle',actions:['read_title'],runs:0,active_runs:0,created_at:stamp}];
    else if(p==='/api/proxies')data=[];
    else if(p==='/api/logs')data=url.searchParams.has('profile_id')?logs:[];
    else if(p==='/api/page-recipient/config')data={asset_root:'fixture-assets'};
    else if(p==='/api/page-recipient/jobs')data=jobs;
    else if(p==='/api/facebook/refresh-status')data={enabled:false,interval_hours:24,running:false,eligible_count:0,due_count:0,next_due_at:null,last_finished_at:null,active_profile_id:null,profiles:[]};
    else if(p==='/api/settings')data={allowed_origins:[],max_browsers:2,max_memory_percent:85,max_cpu_percent:90,min_available_memory_mb:512,action_delay_ms:500,launch_timeout_seconds:30,task_timeout_seconds:300,require_proxy:false,proxy_ip_version:'auto',proxy_routing:'all'};
    else{unexpected.push(p);return route.fulfill({status:404,json:{detail:'Unknown fixture route'}})}
    return route.fulfill({json:data});
   }
   const file=path.resolve(dist,'.'+(p==='/'?'/index.html':p));
   if(!file.startsWith(dist+path.sep)||!fs.existsSync(file)){unexpected.push(p);return route.abort()}
   return route.fulfill({path:file,contentType:file.endsWith('.js')?'application/javascript':file.endsWith('.css')?'text/css':file.endsWith('.woff2')?'font/woff2':'text/html'});
  });
  await page.goto('http://127.0.0.1:8000/');
  await page.getByRole('button',{name:'Fixture Facebook 1',exact:true}).click();
  const dialog=page.getByRole('dialog'),fbNav=dialog.getByRole('navigation',{name:'Pages trong profile pagination',exact:true}),pageCards=dialog.locator('.facebook-page-card');
  await countIs(pageCards,10);await textMatches(fbNav,/1–10 of 26/);
  await fbNav.getByRole('button',{name:'Last page',exact:true}).click();
  await countIs(pageCards,6);await textMatches(fbNav,/21–26 of 26/);
  assert.equal(await fbNav.getByRole('button',{name:'Next page',exact:true}).isDisabled(),true);
  await dialog.getByRole('textbox',{name:'Tìm Page trong profile',exact:true}).fill('026');
  await countIs(pageCards,1);await textMatches(fbNav,/1–1 of 1/);
  await dialog.getByRole('textbox',{name:'Tìm Page trong profile',exact:true}).fill('');
  await dialog.getByRole('combobox',{name:'Lọc trạng thái Page trong profile',exact:true}).selectOption('posted');
  await countIs(pageCards,10);await textMatches(fbNav,/of 13/);
  await fbNav.getByRole('button',{name:'Next page',exact:true}).click();await countIs(pageCards,3);
  await fbNav.getByRole('combobox',{name:'Rows per page',exact:true}).selectOption('25');
  await countIs(pageCards,13);await textMatches(fbNav,/1–13 of 13/);
  await dialog.getByRole('combobox',{name:'Lọc trạng thái Page trong profile',exact:true}).selectOption('all');
  await fbNav.getByRole('combobox',{name:'Rows per page',exact:true}).selectOption('10');
  await fbNav.getByRole('button',{name:'Last page',exact:true}).click();
  // A refreshed profile can lose Pages while its detail dialog stays open.
  await page.clock.install();profiles[0].facebook.pages=cachedPages.slice(0,4);
  await page.clock.fastForward(5001);
  await page.waitForFunction(()=>document.querySelectorAll('.facebook-page-card').length===4);await countIs(pageCards,4);await textMatches(fbNav,/1–4 of 4/);
  profiles[0].facebook.pages=cachedPages;await page.clock.fastForward(5001);
  await pageCards.nth(9).waitFor();await textMatches(fbNav,/1–10 of 26/,'A shrunken list clamps stored page state before later growth');
  assert.equal(await dialog.getByRole('button',{name:'Kiểm tra Facebook',exact:true}).isEnabled(),true,'Existing check control remains available without being invoked');
  await dialog.getByRole('button',{name:'Close dialog',exact:true}).click();
  await page.getByRole('button',{name:'Profile logs',exact:true}).click();
  const logNav=dialog.getByRole('navigation',{name:'events pagination',exact:true}),logRows=dialog.locator('.log-view>div');
  await logRows.first().waitFor();await countIs(logRows,25);await textMatches(logNav,/1–25 of 55/);
  await logNav.getByRole('button',{name:'Last page',exact:true}).click();await countIs(logRows,5);
  await dialog.getByRole('textbox',{name:'Search activity',exact:true}).fill('055');await countIs(logRows,1);await textMatches(logNav,/1–1 of 1/);
  await dialog.getByRole('textbox',{name:'Search activity',exact:true}).fill('');
  await dialog.getByRole('combobox',{name:'Filter activity level',exact:true}).selectOption('error');await textMatches(logNav,/of 28/);
  await logNav.getByRole('button',{name:'Next page',exact:true}).click();await countIs(logRows,3);
  await logNav.getByRole('combobox',{name:'Rows per page',exact:true}).selectOption('50');await countIs(logRows,28);await textMatches(logNav,/1–28 of 28/);
  await dialog.getByRole('button',{name:'Close dialog',exact:true}).click();
  await page.getByRole('checkbox',{name:'Select Fixture Profile 001',exact:true}).check();
  await page.getByRole('button',{name:'Run Task Selected',exact:true}).click();
  const pickerNav=dialog.getByRole('navigation',{name:'automation profiles pagination',exact:true}),pickers=dialog.locator('.profile-picker input');
  await pickers.first().waitFor();await countIs(pickers,10);await textMatches(pickerNav,/1–10 of 26/);
  await pickerNav.getByRole('button',{name:'Next page',exact:true}).click();
  await dialog.getByRole('checkbox',{name:'Fixture Profile 011 No proxy',exact:true}).check();
  await pickerNav.getByRole('button',{name:'Previous page',exact:true}).click();assert.equal(await dialog.getByRole('checkbox',{name:'Fixture Profile 001 Proxy #1',exact:true}).isChecked(),true);
  await pickerNav.getByRole('button',{name:'Next page',exact:true}).click();assert.equal(await dialog.getByRole('checkbox',{name:'Fixture Profile 011 No proxy',exact:true}).isChecked(),true,'Selected profile IDs stay selected across pages');
  await dialog.getByRole('button',{name:'Select matching',exact:true}).click();await dialog.getByRole('button',{name:'Queue 25 runs',exact:true}).waitFor();
  await pickerNav.getByRole('button',{name:'Last page',exact:true}).click();await countIs(pickers,6);
  assert.equal(await dialog.getByRole('checkbox',{name:'Fixture Profile 026 Stop manual browser first',exact:true}).isDisabled(),true,'Bulk matching excludes a running manual profile on another page');
  await dialog.getByRole('textbox',{name:'Search automation profiles',exact:true}).fill('025');await countIs(pickers,1);await textMatches(pickerNav,/1–1 of 1/);
  await dialog.getByRole('textbox',{name:'Search automation profiles',exact:true}).fill('');
  await pickerNav.getByRole('combobox',{name:'Rows per page',exact:true}).selectOption('25');await countIs(pickers,25);await textMatches(pickerNav,/1–25 of 26/);
  assert.deepEqual(writes,[],'Pagination buttons inside the task form must never submit or queue runs');
  for(const width of [960,600]){await page.setViewportSize({width,height:900});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,'No run picker page overflow at '+width)}
  await dialog.getByRole('button',{name:'Cancel',exact:true}).click();await page.setViewportSize({width:1600,height:1050});
  await page.locator('.sidebar').getByRole('button',{name:'Settings',exact:true}).click();
  await page.getByRole('tab',{name:'Nhận Page & Setup',exact:true}).click();
  const jobsSection=page.getByRole('region',{name:'Tiến trình nhận Page',exact:true}),jobNav=jobsSection.getByRole('navigation',{name:'tiến trình nhận Page pagination',exact:true}),jobCards=jobsSection.getByRole('article');
  await jobCards.first().waitFor();await countIs(jobCards,10);await textMatches(jobNav,/1–10 of 26/);
  const form=page.locator('form.recipient-form');await form.getByRole('textbox',{name:'Tên Page dự kiến',exact:true}).fill('Keep this local draft');
  await jobNav.getByRole('button',{name:'Next page',exact:true}).click();
  const code=jobsSection.getByLabel('Mã thủ công cho Fixture Profile 011',{exact:true});await code.fill('112233');
  await jobNav.getByRole('button',{name:'Next page',exact:true}).click();await countIs(jobCards,6);
  await jobNav.getByRole('button',{name:'Previous page',exact:true}).click();assert.equal(await code.inputValue(),'112233','Manual code draft stays with the profile across pagination');
  await jobsSection.getByRole('textbox',{name:'Tìm tiến trình nhận Page',exact:true}).fill('026');await countIs(jobCards,1);await textMatches(jobNav,/1–1 of 1/);
  await jobsSection.getByRole('textbox',{name:'Tìm tiến trình nhận Page',exact:true}).fill('');
  await jobsSection.getByRole('combobox',{name:'Lọc trạng thái nhận Page',exact:true}).selectOption('waiting_code');await countIs(jobCards,10);await textMatches(jobNav,/of 13/);
  await jobNav.getByRole('button',{name:'Next page',exact:true}).click();await countIs(jobCards,3);
  await jobNav.getByRole('combobox',{name:'Rows per page',exact:true}).selectOption('25');await countIs(jobCards,13);await textMatches(jobNav,/1–13 of 13/);
  await jobsSection.getByRole('combobox',{name:'Lọc trạng thái nhận Page',exact:true}).selectOption('all');
  await jobNav.getByRole('combobox',{name:'Rows per page',exact:true}).selectOption('10');
  await jobNav.getByRole('button',{name:'Last page',exact:true}).click();jobs=originalJobs.slice(0,5);
  await jobsSection.getByRole('button',{name:'Làm mới tiến trình',exact:true}).click();await page.waitForFunction(()=>document.querySelectorAll('.recipient-job').length===5);await countIs(jobCards,5);await textMatches(jobNav,/1–5 of 5/);
  jobs=[...originalJobs];await jobsSection.getByRole('button',{name:'Làm mới tiến trình',exact:true}).click();await jobCards.nth(9).waitFor();await textMatches(jobNav,/1–10 of 26/);
  assert.equal(await form.getByRole('textbox',{name:'Tên Page dự kiến',exact:true}).inputValue(),'Keep this local draft');
  for(const width of [960,600]){await page.setViewportSize({width,height:900});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,'No recipient/pagination page overflow at '+width)}
  assert.deepEqual(writes,[],'Navigation, filtering and pagination never trigger mutations');assert.deepEqual(errors,[]);assert.deepEqual(unexpected,[]);
  console.log('PASS: Chrome recipient jobs, Facebook detail Pages, activity logs and automation profile picker paginate after filtering; complete totals, query/status/size resets, last-page clamp after live shrink, draft/selection preservation, full-result bulk selection with manual-profile guard, responsive UI, no form submission or writes. Offline synthetic fixtures only.');
 }finally{await browser.close()}
})().catch(error=>{console.error(error);process.exit(1)});
