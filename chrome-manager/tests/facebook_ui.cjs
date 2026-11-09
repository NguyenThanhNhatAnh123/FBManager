const countIs=async(locator,value,message)=>{for(let n=0;n<100;n++){if(await locator.count()===value)return;await new Promise(resolve=>setTimeout(resolve,50))}const result=await locator.count();require('node:assert/strict').equal(result,value,message)};
const {chromium}=require('../frontend/node_modules/playwright');
const ts=require('../frontend/node_modules/typescript');
const fs=require('node:fs');
const path=require('node:path');
const assert=require('node:assert/strict');
const root=path.resolve(__dirname,'..'),dist=path.join(root,'frontend','dist');
const source=fs.readFileSync(path.join(root,'frontend/src/services/facebook.ts'),'utf8');
const compiled=ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText;
const helpers={};new Function('exports',compiled)(helpers);
for(const [zone,date,hours,day] of [
 ['America/Los_Angeles','2026-03-08T18:00:00Z',23,'2026-03-08'],
 ['America/Los_Angeles','2026-11-01T18:00:00Z',25,'2026-11-01'],
 ['Asia/Ho_Chi_Minh','2026-10-28T20:00:00Z',24,'2026-10-29'],
 ['America/Los_Angeles','2026-10-29T06:59:00Z',24,'2026-10-28'],
]){
 const window=helpers.facebookDay(zone,new Date(date));
 assert.equal(window.check_date,day);assert.equal((Date.parse(window.day_end)-Date.parse(window.day_start))/3600000,hours);
}
(async()=>{
 const output=path.join(root,'.runtime','ui');fs.mkdirSync(output,{recursive:true});
 const browser=await chromium.launch({channel:'chrome',headless:true});
 try{
  const page=await browser.newPage({viewport:{width:1720,height:1080}}),errors=[];
  require('./workspace_fixture.cjs').installWorkspaceFixture(page);
  page.on('pageerror',e=>errors.push(e.message));
  const today=helpers.facebookDay('America/Los_Angeles');
  const fp=(id,name,status)=>({id,name,url:'https://www.facebook.com/TestPage'+id,status,checked_at:new Date().toISOString(),check_date:today.check_date,timezone:today.timezone,posts_today:status==='posted'?2:status==='not_seen'?0:null,posts:status==='posted'?[{url:'https://www.facebook.com/TestPage'+id+'/posts/123',date:today.check_date}]:[],error_message:null});
  const profiles=[
   {id:1,name:'Chrome_001',proxy_id:1,proxy:{host:'192.0.2.1',port:8080,protocol:'http',ip_address:'2001:db8::1',has_credentials:true},browser_status:'Stopped',task:null,facebook:{name:'Tài khoản thử nghiệm A',url:'https://www.facebook.com/AccountA',status:'complete',checked_at:new Date().toISOString(),check_date:today.check_date,timezone:today.timezone,fresh:true,pages:[fp(1,'Page Áo Việt','posted'),fp(2,'Page Trang Trí','not_seen'),fp(3,'Page Chưa Đọc','unknown'),fp(4,'Page Thứ Tư','unchecked')]}},
   {id:2,name:'Chrome_002',proxy_id:2,proxy:{host:'192.0.2.2',port:8080,protocol:'http',ip_address:'2001:db8::2',has_credentials:false},browser_status:'Running',task:null,facebook:{name:'Tài khoản thử nghiệm B',status:'partial',checked_at:null,timezone:today.timezone,fresh:true,pages:[fp(5,'Page Đang Mở','unchecked')]}},
   {id:3,name:'NoProxy',proxy_id:null,proxy:null,browser_status:'Stopped',task:null,facebook:null},
  ];
  const system={cpu_percent:1,memory_percent:20,memory_used_mb:2048,memory_total_mb:16384,memory_available_mb:14336,total_profiles:3,running_browsers:1,queued_browsers:0,stopped_browsers:2,working_proxies:2,failed_proxies:0,total_proxies:2,running_tasks:0,pending_tasks:0,resource_warning:null,max_browsers:5,checking_proxies:0};
  const settings={max_browsers:5,max_memory_percent:85,max_cpu_percent:90,min_available_memory_mb:512,action_delay_ms:500,launch_timeout_seconds:30,task_timeout_seconds:300,require_proxy:true,proxy_ip_version:'auto',proxy_routing:'all',allowed_origins:['https://fixture.example'],facebook_auto_refresh:true};
  let submitted;
  await page.routeWebSocket('**/ws',ws=>ws.close());
  await page.route('http://127.0.0.1:8000/**',async route=>{
   const u=new URL(route.request().url()),p=u.pathname;
   if(p.startsWith('/api/')){
    let body={};
    if(p==='/api/profiles')body=profiles;
    else if(p==='/api/system/status')body=system;
    else if(p==='/api/tasks/history')body={total:0,items:[]};
    else if(p==='/api/settings')body=settings;
    else if(p==='/api/facebook/refresh-status')body={enabled:true,interval_hours:24,running:false,eligible_count:2,due_count:0,next_due_at:new Date(Date.now()+86400000).toISOString(),last_finished_at:null,active_profile_id:null,profiles:[]};
    else if(['/api/proxies','/api/tasks','/api/logs'].includes(p))body=[];
    else if(p==='/api/facebook/check'){
     submitted=route.request().postDataJSON();profiles[0].facebook.status='queued';body={accepted:submitted.profile_ids,errors:[]};
    }else if(p==='/api/facebook/1/stop'){profiles[0].facebook.status='cancelled';body={ok:true};}
    return route.fulfill({json:body});
   }
   const file=path.resolve(dist,'.'+(p==='/'?'/index.html':p));
   if(!file.startsWith(dist+path.sep)||!fs.existsSync(file))return route.abort();
   return route.fulfill({path:file,contentType:file.endsWith('.js')?'application/javascript':file.endsWith('.css')?'text/css':file.endsWith('.woff2')?'font/woff2':'text/html'});
  });
  await page.goto('http://127.0.0.1:8000/');
  await page.getByText('Tài khoản thử nghiệm A',{exact:true}).waitFor();
  await countIs(page.locator('.profile-table tbody tr'),2,'dashboard preserves proxy-only scope');
  await countIs(page.getByText('Page Áo Việt',{exact:true}),1);
  await countIs(page.getByText('Page Trang Trí',{exact:true}),1);
  await countIs(page.getByText('Page Thứ Tư',{exact:true}),0,'preview shows first 3 pages');
  assert.deepEqual(await page.locator('.facebook-account-cell').allTextContents(),['Tài khoản thử nghiệm A','Tài khoản thử nghiệm B'],'Facebook cells show names only');
  await countIs(page.locator('.facebook-row-check'),0,'Dashboard has no per-row Facebook check controls');
  await page.getByRole('button',{name:'Tài khoản thử nghiệm B',exact:true}).click();
  assert.equal(await page.getByRole('dialog').getByRole('button',{name:'Kiểm tra Facebook',exact:true}).isDisabled(),true,'Manual browser must stop before checking from the detail dialog');
  await page.getByRole('dialog').getByRole('button',{name:'Close dialog',exact:true}).click();
  // Regression: dragging selected text out of the input/dialog must not dismiss Edit.
  await page.getByRole('row').filter({hasText:'Chrome_001'}).getByRole('button',{name:'Edit profile',exact:true}).click();
  const editDialog=page.getByRole('dialog'),nameInput=editDialog.getByRole('textbox',{name:'Profile name',exact:true});
  assert.equal(await nameInput.inputValue(),'Chrome_001');
  const dialogBox=await editDialog.boundingBox(),inputBox=await nameInput.boundingBox();
  await page.mouse.move(inputBox.x+140,inputBox.y+inputBox.height/2);
  await page.mouse.down();
  await page.mouse.move(dialogBox.x-15,inputBox.y+inputBox.height/2,{steps:12});
  await page.mouse.up();
  await countIs(editDialog,1,'Dragging to select a profile name must not close Edit');
  await nameInput.press('Control+A');
  await page.keyboard.insertText('Tên mới sau khi dán');
  assert.equal(await nameInput.inputValue(),'Tên mới sau khi dán');
  await page.mouse.click(dialogBox.x+4,inputBox.y+inputBox.height/2);
  await countIs(editDialog,1,'Clicking padding inside the dialog must not count as backdrop');
  await editDialog.getByRole('button',{name:'Cancel',exact:true}).click();
  await countIs(editDialog,0);
  await countIs(page.getByText('Chrome_001',{exact:true}),1,'Cancel keeps the original profile name');
  await page.getByRole('row').filter({hasText:'Chrome_001'}).getByRole('button',{name:'Edit profile',exact:true}).click();
  const reopenedBox=await editDialog.boundingBox();
  await page.mouse.click(reopenedBox.x-15,reopenedBox.y+40);
  await countIs(editDialog,0,'A genuine outside click still dismisses the dialog');
  await page.screenshot({path:path.join(output,'facebook-dashboard.png'),fullPage:true});
  await page.getByRole('textbox',{name:'Search profiles'}).fill('Trang Trí');
  await countIs(page.locator('.profile-table tbody tr'),1);
  await page.getByRole('textbox',{name:'Search profiles'}).fill('');
  await page.getByRole('combobox',{name:'Lọc bài đăng hôm nay'}).selectOption('posted');
  await countIs(page.locator('.profile-table tbody tr'),1);
  await page.getByRole('combobox',{name:'Lọc bài đăng hôm nay'}).selectOption('all');
  await page.getByRole('button',{name:'+1 Page khác · Xem chi tiết (4 Page)',exact:true}).click();
  await page.getByRole('dialog').waitFor();
  await countIs(page.locator('.facebook-page-card'),4);
  assert.equal(await page.getByRole('link',{name:'Bài hôm nay #1'}).getAttribute('href'),'https://www.facebook.com/TestPage1/posts/123');
  await page.screenshot({path:path.join(output,'facebook-detail.png'),fullPage:true});
  await page.getByRole('button',{name:'Close dialog'}).click();
  await page.getByRole('checkbox',{name:'Select Chrome_001',exact:true}).check();
  await page.getByRole('button',{name:'Settings',exact:true}).click();
  await page.getByRole('tab',{name:'Dữ liệu & công cụ',exact:true}).click();
  await page.getByRole('combobox',{name:'Múi giờ kiểm tra Facebook'}).selectOption('Asia/Ho_Chi_Minh');
  await page.getByRole('button',{name:'Dashboard',exact:true}).click();
  await countIs(page.locator('.facebook-page-row .green'),0,'different timezone must not reuse today counts');
  await page.getByRole('button',{name:'Settings',exact:true}).click();
  await page.getByRole('tab',{name:'Dữ liệu & công cụ',exact:true}).click();
  await page.getByRole('combobox',{name:'Múi giờ kiểm tra Facebook'}).selectOption('America/Los_Angeles');
  await page.getByRole('button',{name:'Kiểm tra đã chọn (1)',exact:true}).click();
  await page.getByText('Đã xếp hàng kiểm tra Facebook cho 1 profile',{exact:true}).waitFor();
  assert.deepEqual(submitted.profile_ids,[1]);assert.equal(submitted.timezone,'America/Los_Angeles');assert.equal(submitted.check_date,today.check_date);
  await page.getByRole('button',{name:'Dashboard',exact:true}).click();
  await page.getByRole('button',{name:'Tài khoản thử nghiệm A',exact:true}).click();
  const facebookDialog=page.getByRole('dialog');
  await facebookDialog.getByText(/^Chờ kiểm tra/).waitFor();
  await facebookDialog.getByRole('button',{name:'Dừng kiểm tra',exact:true}).click();
  await facebookDialog.getByText(/^Đã dừng/).waitFor();
  assert.equal(await facebookDialog.getByRole('button',{name:'Kiểm tra Facebook',exact:true}).isDisabled(),false,'Stopped profile can be checked manually from its details');
  await facebookDialog.getByRole('button',{name:'Kiểm tra Facebook',exact:true}).click();
  await facebookDialog.getByText(/^Chờ kiểm tra/).waitFor();
  await facebookDialog.getByRole('button',{name:'Dừng kiểm tra',exact:true}).click();
  await facebookDialog.getByText(/^Đã dừng/).waitFor();
  await facebookDialog.getByRole('button',{name:'Close dialog',exact:true}).click();
  for(const width of [960,600]){
   await page.setViewportSize({width,height:900});
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,'no page overflow at '+width);
  }
  // Exercise the actual DOM reader against representative rendered Facebook UI.
  const dom=new Function('return ('+fs.readFileSync(path.join(root,'backend/app/services/facebook_dom.js'),'utf8')+')')();
  await page.setContent('<main aria-hidden="true"><h1>Stale Name</h1></main><main role="main"><div role="heading" aria-level="1">Tên tài khoản thật</div></main>');
  let identity=await page.evaluate(dom,{mode:'account'});
  assert.equal(identity.name,'Tên tài khoản thật');assert.equal(identity.source,'profile_heading');
  await page.setContent('<title>(1) Facebook</title><link rel="canonical" href="https://www.facebook.com/profile.php?id=123"><main role="main"><div role="button"><span style="font-size:32px;font-weight:700">Julie Mendoza</span></div><h2>Vous connaissez peut-être...</h2><div role="article"><span style="font-size:40px;font-weight:700">Unrelated post heading</span></div></main>');
  identity=await page.evaluate(dom,{mode:'account'});
  assert.equal(identity.name,'Julie Mendoza');assert.equal(identity.source,'profile_display_name');
  await page.setContent('<title>(2) Jane Doe | Facebook</title><link rel="canonical" href="https://www.facebook.com/JaneDoe"><main role="main"><p>Profile layout with no h1</p></main>');
  identity=await page.evaluate(dom,{mode:'account'});
  assert.equal(identity.name,'Jane Doe');assert.equal(identity.source,'profile_title');assert.equal(identity.url,'https://www.facebook.com/JaneDoe');
  await page.setContent('<meta property="og:title" content="Jane Doe"><meta property="og:url" content="https://www.facebook.com/profile.php?id=123"><main role="main"></main>');
  identity=await page.evaluate(dom,{mode:'account'});
  assert.equal(identity.name,'Jane Doe');assert.equal(identity.url,'https://www.facebook.com/profile.php?id=123');
  await page.setContent('<div role="menu"><a href="https://www.facebook.com/profile.php?id=123"><div>Jane Doe</div><div>See your profile</div></a><a href="https://www.facebook.com/logout.php">Log out</a></div>');
  identity=await page.evaluate(dom,{mode:'account',accountMenu:true});
  assert.equal(identity.name,'Jane Doe');assert.equal(identity.source,'account_menu');
  await page.setContent('<title>Facebook</title><main role="main"></main><div role="menu"><a href="https://www.facebook.com/logout.php">Log out</a></div>');
  identity=await page.evaluate(dom,{mode:'account',accountMenu:true});
  assert.equal(identity.name,'','do not treat generic Facebook title or a logout action as the account name');
  await page.setContent('<main role="main"><h1>Pages you manage</h1><section><h2>Pages you manage</h2><a href="https://www.facebook.com/Owned"><img alt="">Owned Page</a><h2>Suggested Pages</h2><h3><a href="https://www.facebook.com/Suggested"><img alt="">Suggested Page</a></h3></section></main>');
  let data=await page.evaluate(dom,{mode:'pages'});
  assert.equal(data.recognized,true);assert.deepEqual(data.pages.map(p=>p.name),['Owned Page']);
  await page.setContent('<main role="main"><h1>Pages you manage</h1><div role="listitem"><a href="https://www.facebook.com/ManagementTips"><span>Management Tips</span></a><a href="https://www.facebook.com/ManagementTips">Manage Page</a></div></main>');
  data=await page.evaluate(dom,{mode:'pages'});
  assert.deepEqual(data.pages.map(p=>p.name),['Management Tips']);
  await page.setContent('<main role="main"><h1>Pages que vous gérez</h1><div role="listitem"><a href="https://www.facebook.com/PageFrance">Page France</a><a href="https://www.facebook.com/PageFrance">Gérer la page</a></div><h2>Pages suggérées</h2><div role="listitem"><a href="https://www.facebook.com/Suggestion">Suggestion</a></div></main>');
  data=await page.evaluate(dom,{mode:'pages'});
  assert.equal(data.recognized,true);assert.deepEqual(data.pages.map(p=>p.name),['Page France']);
  await page.setContent('<main role="main"><h1>Pages</h1><section><span style="font-size:20px;font-weight:700">Pages que vous gérez</span><div><a href="https://www.facebook.com/profile.php?id=123"><span style="font-size:20px;font-weight:700">London Luca</span></a><a href="https://www.facebook.com/profile.php?id=123&amp;sk=notifications">2 notifications</a></div></section></main>');
  data=await page.evaluate(dom,{mode:'pages'});
  assert.equal(data.recognized,true);assert.deepEqual(data.pages.map(p=>p.name),['London Luca']);
  await page.setContent('<main role="main"><h1>Owned Page</h1><div role="article"><h3><a href="https://www.facebook.com/Owned">Owned Page</a></h3><a href="https://www.facebook.com/Owned/posts/123" aria-label="October 28, 2026 at 9:00 AM"><span>2h</span></a><p>Post body</p><div role="article"><h3><a href="https://www.facebook.com/Visitor">Visitor</a></h3><a href="https://www.facebook.com/Visitor/posts/456">1h</a></div></div></main>');
  data=await page.evaluate(dom,{mode:'posts',pageName:'Owned Page'});
  assert.equal(data.posts.length,1);assert.equal(data.posts[0].author_matches,true);assert.ok(data.posts[0].times.includes('October 28, 2026 at 9:00 AM'));
  await page.setContent('<main role="main"><input name="email"><input name="pass"></main>');
  assert.equal((await page.evaluate(dom,{mode:'account'})).blocked,true);
  assert.deepEqual(errors,[]);
  console.log('PASS: Edit stays open when dragging selected text outside, replacement text, padding clicks, Cancel and true backdrop clicks; DST-aware day windows; name-only FB cells and multiple Pages, per-Page badges, search/filter, details/post links, Settings data-tools timezone/bulk check, detail-dialog manual check/cancellation and busy-profile guard, responsive layout, DOM extraction excluding suggestions/nested shares, and login detection. Test fixtures only; live Facebook access not tested.');
 }finally{await browser.close()}
})().catch(e=>{console.error(e);process.exit(1)});
