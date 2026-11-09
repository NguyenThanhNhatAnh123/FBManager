// Offline clock/config/catalog fixtures. No real history, schedules or writes.
const {chromium}=require('../frontend/node_modules/playwright');
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const dist=path.resolve(process.env.CM_UI_DIST||path.join(__dirname,'../frontend/dist')),origin='http://127.0.0.1:8000';
const settle=async check=>{for(let i=0;i<120;i++){if(await check())return;await new Promise(resolve=>setTimeout(resolve,25))}assert.fail('UI did not settle')};
(async()=>{
 const browser=await chromium.launch({channel:'chrome',headless:true});
 try{
  const context=await browser.newContext({offline:true,serviceWorkers:'block',viewport:{width:1200,height:900}}),page=await context.newPage();
  const writes=[],outside=[],errors=[],listReads=[],pageReads=[];let releaseConfig=null,eligible=true;
  await page.clock.setFixedTime(new Date('2026-11-05T02:00:00Z')); // LA is Nov4; configured HCM is Nov5.
  require('./workspace_fixture.cjs').installWorkspaceFixture(page);
  page.on('request',request=>{const url=new URL(request.url());if(url.pathname==='/api/v1/reels')listReads.push(url);if(url.pathname==='/api/v1/reel-page-options')pageReads.push(url)});
  page.on('pageerror',error=>errors.push(error.message));
  const profiles=[{id:1,name:'Today fixture profile',profile_path:'inert/profile',proxy_id:null,proxy:null,browser_status:'Stopped',browser_mode:null,browser_error:null,task:null,facebook:null,outlook:null,bm:null}];
  const system={cpu_percent:1,memory_percent:20,memory_used_mb:2048,memory_total_mb:16384,memory_available_mb:14336,total_profiles:1,running_browsers:0,queued_browsers:0,stopped_browsers:1,working_proxies:0,failed_proxies:0,total_proxies:0,running_tasks:0,pending_tasks:0,resource_warning:null,max_browsers:2,checking_proxies:0};
  await page.routeWebSocket('**/ws',socket=>socket.close());
  await page.route('**/*',async route=>{
   const req=route.request(),url=new URL(req.url()),p=url.pathname;
   if(url.origin!==origin){outside.push(url.origin);return route.abort()}
   if(req.method()!=='GET'){writes.push(p);return route.abort()}
   if(p.startsWith('/api/')){
    let value;
    if(p==='/api/session')value={};else if(p==='/api/profiles')value=profiles;else if(p==='/api/system/status')value=system;
    else if(p==='/api/tasks/history')value={items:[],total:0};
    else if(['/api/proxies','/api/tasks','/api/logs','/api/page-recipient/jobs','/api/reels','/api/reels/assets'].includes(p))value=[];
    else if(p==='/api/reels/config'){
     await new Promise(resolve=>{releaseConfig=resolve});
     value={max_upload_bytes:1073741824,accepted_extensions:['.mp4'],default_timezone:'Asia/Ho_Chi_Minh',scheduler_running:true,background_browser:true};
    }else if(p==='/api/reels/pages'){
     const entry=(id,ready,changes={})=>({page_url:'https://www.facebook.com/profile.php?id='+id,page_name:'Page '+id,source:'received',page_id:id,can_publish:ready,information_status:ready?'updated':'not_updated',posting_block_reason:ready?null:'page_information_required',posting_revision:ready?1:0,...changes});
     value=[entry('111',eligible),entry('222',false),entry('333',true,{information_status:'not_updated'}),entry('0',true)]; // Intentionally unfiltered to exercise the client guard too.
    }else if(p==='/api/reels/page-eligibility')value={page_id:'111',can_publish:eligible,information_status:eligible?'updated':'not_updated',posting_block_reason:eligible?null:'page_information_required',posting_revision:eligible?1:0};
    else return route.fulfill({status:404,json:{detail:'Unknown inert endpoint'}});
    return route.fulfill({json:value});
   }
   const file=path.resolve(dist,'.'+(p==='/'?'/index.html':p));if(!file.startsWith(dist+path.sep)||!fs.existsSync(file))return route.abort();
   return route.fulfill({path:file,contentType:file.endsWith('.js')?'application/javascript':file.endsWith('.css')?'text/css':'text/html'});
  });
  await page.goto(origin+'/');const nav=page.getByRole('navigation',{name:'Điều hướng chính',exact:true});
  await nav.getByRole('button',{name:'Lịch đã lưu',exact:true}).click();await settle(()=>!!releaseConfig);
  await page.waitForTimeout(100);assert.equal(listReads.length,0,'Config must settle before the first saved-list request');
  assert.equal(await page.getByLabel('Lọc thời gian lịch Reel',{exact:true}).inputValue(),'today');
  releaseConfig();await settle(()=>listReads.length>0);
  assert.equal(listReads[0].searchParams.get('calendar'),'2026-11-05');assert.equal(listReads[0].searchParams.get('limit'),'10');
  assert.ok(listReads.every(url=>url.searchParams.has('calendar')),'No initial unfiltered history read');
  await page.getByText('Hôm nay: 05/11/2026 · Asia/Ho_Chi_Minh',{exact:true}).waitFor();
  await page.clock.setFixedTime(new Date('2026-11-05T17:00:01Z'));
  await settle(()=>listReads.some(url=>url.searchParams.get('calendar')==='2026-11-06'));
  await page.getByLabel('Lọc thời gian lịch Reel',{exact:true}).selectOption('day');
  const input=page.getByLabel('Giá trị thời gian lịch Reel',{exact:true});await input.fill('2027-03-04');
  await settle(()=>listReads.at(-1)?.searchParams.get('calendar')==='2027-03-04');
  await page.clock.setFixedTime(new Date('2026-11-06T17:00:01Z'));await page.waitForTimeout(1100);
  assert.equal(await input.inputValue(),'2027-03-04','Midnight never overwrites a manual period');
  assert.equal(listReads.at(-1).searchParams.get('calendar'),'2027-03-04');
  await page.getByLabel('Lọc thời gian lịch Reel',{exact:true}).selectOption('all');await settle(()=>!listReads.at(-1)?.searchParams.has('calendar'));
  await nav.getByRole('button',{name:'Lịch đăng Page',exact:true}).click();
  const choice=page.getByLabel('Page đăng Reel',{exact:true});await settle(async()=>await choice.locator('option').count()===5&&await choice.isEnabled());
  for(const text of ['Page 222','Page 333','Page 0'])assert.equal(await choice.locator('option').filter({hasText:text}).evaluate(option=>option.disabled),true,'Unconfigured or invalid Pages are visible but cannot be selected');
  assert.ok(pageReads.every(url=>url.searchParams.get('limit')==='25'&&url.searchParams.get('profile_id')==='1'));
  eligible=false;await page.getByRole('button',{name:'Làm mới Page',exact:true}).click();
  await settle(async()=>await choice.locator('option').count()===5&&await choice.isEnabled());
  assert.equal(await choice.inputValue(),'manual');assert.equal(await page.getByLabel('Link Page thủ công',{exact:true}).inputValue(),'');
  assert.deepEqual(writes,[]);assert.deepEqual(outside,[]);assert.deepEqual(errors,[]);
  console.log('PASS: Today before first list GET, delayed configured timezone, local-midnight rollover, manual dates preserved, explicit All, eligible-only Page query and fail-closed picker/removal; zero writes.');
  await context.close();
 }finally{await browser.close()}
})().catch(error=>{console.error(error);process.exitCode=1});
