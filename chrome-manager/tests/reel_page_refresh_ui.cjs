// Disposable offline browser and synthetic GET responses only; no real app writes.
const {chromium}=require('../frontend/node_modules/playwright');
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const dist=path.resolve(process.env.CM_UI_DIST||path.join(__dirname,'../frontend/dist')),origin='http://127.0.0.1:8000';
const waitFor=async check=>{for(let i=0;i<100;i++){if(await check())return;await new Promise(resolve=>setTimeout(resolve,30))}assert.fail('UI condition did not settle')};
(async()=>{
 const browser=await chromium.launch({channel:'chrome',headless:true});
 try{
  const context=await browser.newContext({offline:true,serviceWorkers:'block',viewport:{width:1500,height:1100}}),page=await context.newPage();
  const errors=[],writes=[],outside=[],reads=[];
  let ready=false,withNew=false,removeSelected=false,fail=false,hold=false,release=null;
  require('./workspace_fixture.cjs').installWorkspaceFixture(page);
  page.on('pageerror',error=>errors.push(error.message));
  const target=id=>origin.replace('http://127.0.0.1:8000','https://www.facebook.com')+'/profile.php?id='+id;
  const option=(id,name,source='received')=>({page_url:target(id),page_name:name,source,page_id:id,can_publish:ready,
   information_status:ready?'updated':'not_updated',posting_block_reason:ready?null:'page_information_required',posting_revision:ready?1:0});
  const base={proxy_id:null,proxy:null,browser_status:'Stopped',browser_mode:null,browser_error:null,task:null,facebook:null,outlook:null,bm:null};
  const profiles=[{...base,id:1,name:'Refresh Profile One',profile_path:'fixture/one'},{...base,id:3,name:'Refresh Profile Three',profile_path:'fixture/three'}];
  const system={cpu_percent:1,memory_percent:20,memory_used_mb:2048,memory_total_mb:16384,memory_available_mb:14336,total_profiles:2,running_browsers:0,queued_browsers:0,stopped_browsers:2,working_proxies:0,failed_proxies:0,total_proxies:0,running_tasks:0,pending_tasks:0,resource_warning:null,max_browsers:2,checking_proxies:0};
  await page.routeWebSocket('**/ws',socket=>socket.close());
  await page.route('**/*',async route=>{
   const request=route.request(),url=new URL(request.url()),p=url.pathname;
   if(url.origin!==origin){outside.push(request.url());return route.abort()}
   if(p.startsWith('/api/')){
    if(request.method()!=='GET'){writes.push({path:p,method:request.method()});return route.fulfill({status:405,json:{detail:'Read-only scenario'}})}
    let data;
    if(p==='/api/session')data={};
    else if(p==='/api/profiles')data=profiles;
    else if(p==='/api/system/status')data=system;
    else if(['/api/proxies','/api/tasks','/api/logs','/api/page-recipient/jobs','/api/reels','/api/reels/assets'].includes(p))data=[];
    else if(p==='/api/tasks/history')data={items:[],total:0};
    else if(p==='/api/reels/config')data={max_upload_bytes:1073741824,accepted_extensions:['.mp4'],default_timezone:'America/Los_Angeles',scheduler_running:true,background_browser:true};
    else if(p==='/api/reels/page-eligibility'){
     const value=new URL(url.searchParams.get('page_url'));data=option(value.searchParams.get('id')||'555','Policy fixture');
    }else if(p==='/api/reels/pages'){
     const pid=url.searchParams.get('profile_id');reads.push(pid);
     const items=pid==='1'?[option('111','Profile One Page')]:[option('555','Cached Page','facebook_scan'),...(!removeSelected?[option('777','Selected Page')]:[]),...(withNew?[option('888','Newly Received Page')]:[])];
     if(hold){hold=false;await new Promise(resolve=>{release=resolve})}
     if(fail)return route.fulfill({status:503,json:{detail:'Synthetic page read failed'}});
     data=items;
    }else return route.fulfill({status:404,json:{detail:'Unknown synthetic GET '+p}});
    return route.fulfill({json:data});
   }
   const file=path.resolve(dist,'.'+(p==='/'?'/index.html':p));
   if(!file.startsWith(dist+path.sep)||!fs.existsSync(file))return route.abort();
   return route.fulfill({path:file,contentType:file.endsWith('.js')?'application/javascript':file.endsWith('.css')?'text/css':file.endsWith('.woff2')?'font/woff2':'text/html'});
  });
  await page.goto(origin+'/');
  await page.getByRole('button',{name:'Dashboard',exact:true}).click();
  const nav=page.getByRole('navigation',{name:'Điều hướng chính',exact:true});
  await nav.getByRole('button',{name:'Lịch đăng Page',exact:true}).click();
  const form=page.locator('.reels-form-panel > form.reels-form'),choice=form.getByRole('combobox',{name:'Page đăng Reel',exact:true});
  await form.getByRole('combobox',{name:'Profile đăng Reel',exact:true}).selectOption('3');
  const refresh=()=>form.getByRole('button',{name:'Làm mới Page',exact:true});
  await waitFor(async()=>await choice.locator('option').count()===3&&await choice.isEnabled());
  assert.equal(await choice.locator('option').filter({hasText:'Selected Page'}).evaluate(option=>option.disabled),true,'Unconfigured Pages cannot be selected');
  ready=true;withNew=true;const before=reads.length;
  await refresh().click();
  await waitFor(async()=>await choice.locator('option').count()===4&&await choice.isEnabled());
  assert.equal(reads.length,before+1,'One refresh makes one bounded list GET');
  await choice.selectOption(target('777'));await refresh().click();await waitFor(async()=>await choice.isEnabled());
  assert.equal(await choice.inputValue(),target('777'),'Refreshing preserves an eligible chosen Page');
  assert.match(await choice.locator('option:checked').textContent(),/Đã đổi thông tin/);
  assert.equal(await choice.locator('option').filter({hasText:'Newly Received Page'}).count(),1);
  await waitFor(async()=>await form.getByText('Page đã được xác nhận cập nhật thông tin.',{exact:true}).count()===1);

  await choice.selectOption('manual');
  const manualUrl=form.getByRole('textbox',{name:'Link Page thủ công',exact:true}),manualName=form.getByRole('textbox',{name:'Tên Page thủ công',exact:true});
  await manualUrl.fill(target('999'));await manualName.fill('My manual target');
  fail=true;await refresh().click();
  await waitFor(async()=>await form.getByText(/Synthetic page read failed/).count()===1&&await refresh().isEnabled());
  assert.equal(await choice.inputValue(),'manual');assert.equal(await manualUrl.inputValue(),target('999'));
  assert.equal(await manualName.inputValue(),'My manual target');
  fail=false;await refresh().click();await waitFor(async()=>await refresh().isEnabled());
  await choice.selectOption(target('777'));removeSelected=true;
  await refresh().click();await waitFor(async()=>await choice.inputValue()===target('555')&&await choice.isEnabled());
  await choice.selectOption('manual');
  assert.equal(await manualUrl.inputValue(),'','A Page missing from the eligible catalog is not reintroduced as a manual target');
  assert.equal(await manualName.inputValue(),'');

  hold=true;const heldBefore=reads.length;await refresh().click();
  await waitFor(()=>release!==null);
  const busy=form.getByRole('button',{name:'Đang đọc Page…',exact:true});assert.equal(await busy.isDisabled(),true);
  await busy.evaluate(button=>button.click());assert.equal(reads.length,heldBefore+1,'A busy refresh cannot dispatch another GET');
  await form.getByRole('combobox',{name:'Profile đăng Reel',exact:true}).selectOption('1');
  await waitFor(async()=>await choice.inputValue()===target('111')&&await choice.isEnabled());
  release();release=null;await page.waitForTimeout(100);
  assert.equal(await choice.inputValue(),target('111'),'Late previous-profile results cannot replace the current selection');
  assert.equal(await choice.locator('option').filter({hasText:'Cached Page'}).count(),0);

  hold=true;await refresh().click();await waitFor(()=>release!==null);
  await nav.getByRole('button',{name:'Lịch đã lưu',exact:true}).click();
  release();release=null;await page.waitForTimeout(100);
  await nav.getByRole('button',{name:'Lịch đăng Page',exact:true}).click();
  await waitFor(async()=>await choice.inputValue()===target('111')&&await choice.isEnabled());
  hold=true;await refresh().click();await waitFor(()=>release!==null);
  await page.getByRole('button',{name:'Dashboard',exact:true}).click();
  release();release=null;await page.waitForTimeout(100);
  assert.equal(await form.count(),0,'The scheduling component can unmount with a Page read in flight');
  assert.deepEqual(errors,[]);assert.deepEqual(writes,[]);assert.deepEqual(outside,[]);
  console.log('PASS: Page refresh updates labels/list, preserves cached/manual targets, handles errors/busy/stale reads, and makes zero writes.');
 }finally{await browser.close()}
})().catch(error=>{console.error(error);process.exitCode=1});
