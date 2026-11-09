const countIs=async(locator,value,message)=>{for(let n=0;n<100;n++){if(await locator.count()===value)return;await new Promise(resolve=>setTimeout(resolve,50))}const result=await locator.count();require('node:assert/strict').equal(result,value,message)};
const {chromium}=require('../frontend/node_modules/playwright');
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const root=path.resolve(__dirname,'..'),dist=path.resolve(process.env.CM_UI_DIST||path.join(root,'frontend/dist')),output=path.join(root,'.runtime/ui');
(async()=>{
 fs.mkdirSync(output,{recursive:true});
 const browser=await chromium.launch({channel:'chrome',headless:true});
 try{
  const page=await browser.newPage({offline:true,serviceWorkers:'block',viewport:{width:1440,height:1100}}),errors=[];
  require('./workspace_fixture.cjs').installWorkspaceFixture(page);
  page.on('pageerror',e=>errors.push(e.message));
  const profiles=[{id:1,name:'Profile Outlook Test',proxy_id:1,proxy:{host:'192.0.2.1',port:8080,protocol:'http',ip_address:'2001:db8::1',has_credentials:true},browser_status:'Stopped',browser_mode:null,task:null,facebook:null,outlook:null},
   {id:2,name:'Busy Profile',proxy_id:2,proxy:{host:'192.0.2.2',port:8080,protocol:'http',has_credentials:false},browser_status:'Running',browser_mode:'manual',task:null,facebook:null,outlook:{email:'saved.user@outlook.com',status:'signed_in',message:'Fixture sign-in verified',updated_at:new Date().toISOString(),active:false}}];
  const system={cpu_percent:1,memory_percent:20,memory_used_mb:2048,memory_total_mb:16384,memory_available_mb:14336,total_profiles:2,running_browsers:1,queued_browsers:0,stopped_browsers:1,working_proxies:2,failed_proxies:0,total_proxies:2,running_tasks:0,pending_tasks:0,resource_warning:null,max_browsers:5,checking_proxies:0};
  let startBody,manualBody,opened=false;
  await page.routeWebSocket('**/ws',ws=>ws.close());
  await page.route('http://127.0.0.1:8000/**',async route=>{
   const p=new URL(route.request().url()).pathname;
   if(p.startsWith('/api/')){
    let body={};
    if(p==='/api/profiles')body=profiles;
    else if(p==='/api/system/status')body=system;
    else if(p==='/api/tasks/history')body={total:0,items:[]};
    else if(['/api/proxies','/api/tasks','/api/logs'].includes(p))body=[];
    else if(p==='/api/outlook/start'){
     startBody=route.request().postDataJSON();
     profiles[0].outlook={email:startBody.email,status:'waiting_code',message:'Waiting for a fresh code',updated_at:new Date().toISOString(),active:true};
     body={profile_id:1,status:'queued'};
    }else if(p==='/api/outlook/1/code'){
     manualBody=route.request().postDataJSON();profiles[0].outlook.status='verifying';
    }else if(p==='/api/outlook/1/stop'){
     profiles[0].outlook.status='cancelled';profiles[0].outlook.active=false;
    }else if(p==='/api/outlook/1/open')opened=true;
    return route.fulfill({json:body});
   }
   const file=path.resolve(dist,'.'+(p==='/'?'/index.html':p));
   if(!file.startsWith(dist+path.sep)||!fs.existsSync(file))return route.abort();
   return route.fulfill({path:file,contentType:file.endsWith('.js')?'application/javascript':file.endsWith('.css')?'text/css':file.endsWith('.woff2')?'font/woff2':'text/html'});
  });
  await page.goto('http://127.0.0.1:8000/');
  await page.getByText('Profile Outlook Test',{exact:true}).waitFor();
  await page.getByRole('columnheader',{name:'Outlook',exact:true}).waitFor();
  await countIs(page.getByRole('columnheader',{name:'Task status',exact:true}),0);
  const signedInStatus=page.getByRole('button',{name:'Outlook của Busy Profile: saved.user@outlook.com',exact:true});
  await signedInStatus.waitFor();
  await signedInStatus.scrollIntoViewIfNeeded();
  await page.screenshot({path:path.join(output,'outlook-status-column.png'),fullPage:false});
  await page.getByRole('row').filter({has:page.getByText('Profile Outlook Test',{exact:true})}).getByRole('button',{name:'Outlook đăng nhập và lấy mã',exact:true}).click();
  const dialog=page.getByRole('dialog');await dialog.waitFor();
  assert.equal(await dialog.getByRole('textbox',{name:'Email Outlook',exact:true}).inputValue(),'');
  assert.equal(await dialog.locator('input[aria-label="Mật khẩu Outlook"]').getAttribute('type'),'password');
  await page.screenshot({path:path.join(output,'outlook-login-dialog.png'),fullPage:true});
  await dialog.locator('input[aria-label="Dòng email mật khẩu mail khôi phục"]').fill('sample.user@hotmail.com|FixtureOnly!9|recovery.sample@smvmail.com');
  await dialog.getByRole('button',{name:'Tách vào các ô',exact:true}).click();
  assert.equal(await dialog.getByRole('textbox',{name:'Email Outlook',exact:true}).inputValue(),'sample.user@hotmail.com');
  assert.equal(await dialog.locator('input[aria-label="Mật khẩu Outlook"]').inputValue(),'FixtureOnly!9');
  assert.equal(await dialog.getByRole('textbox',{name:'Email khôi phục',exact:true}).inputValue(),'recovery.sample@smvmail.com');
  assert.equal(await dialog.getByRole('textbox',{name:'Link web lấy mã',exact:true}).inputValue(),'https://smvmail.com/email/inbox?email={email}');
  assert.equal(await dialog.locator('input[aria-label="Dòng email mật khẩu mail khôi phục"]').inputValue(),'');
  const search=await dialog.getByRole('link',{name:'Tìm web đọc mail cho smvmail.com'}).getAttribute('href');
  assert.ok(!decodeURIComponent(search).includes('recovery.sample'),'search sends only the domain');
  for(const domain of ['fviainboxes.com','fviadropinbox.com','fviamail.work','dropinboxes.com']){
   await dialog.getByRole('textbox',{name:'Email khôi phục',exact:true}).fill('recovery.sample@'+domain);
   assert.equal(await dialog.getByRole('textbox',{name:'Link web lấy mã',exact:true}).inputValue(),'https://fviainboxes.com/');
   await dialog.getByText('Đã gợi ý link FVia Inboxes. Bạn có thể thay bằng link riêng.',{exact:false}).waitFor();
  }
  await page.screenshot({path:path.join(output,'outlook-fvia-reader.png'),fullPage:true});
  const inboxesDomains=JSON.parse(fs.readFileSync(path.join(root,'shared/recovery_readers.json'),'utf8')).find(p=>p.name==='Inboxes.com').domains;
  assert.equal(inboxesDomains.length,18);
  for(const domain of inboxesDomains){
   await dialog.getByRole('textbox',{name:'Email khôi phục',exact:true}).fill('recovery.sample@'+domain);
   assert.equal(await dialog.getByRole('textbox',{name:'Link web lấy mã',exact:true}).inputValue(),'https://inboxes.com/');
   await dialog.getByText('Đã gợi ý link Inboxes.com. Bạn có thể thay bằng link riêng.',{exact:false}).waitFor();
  }
  await page.screenshot({path:path.join(output,'outlook-inboxes-reader.png'),fullPage:true});
  await dialog.getByRole('textbox',{name:'Link web lấy mã',exact:true}).fill('https://reader.example/inbox?email={email}');
  await dialog.getByRole('textbox',{name:'Email khôi phục',exact:true}).fill('another@other.example');
  assert.equal(await dialog.getByRole('textbox',{name:'Link web lấy mã',exact:true}).inputValue(),'https://reader.example/inbox?email={email}','custom links are not overwritten');
  await dialog.getByRole('button',{name:'Đăng nhập & lấy mã',exact:true}).click();
  await dialog.getByText('Đang chờ mã',{exact:true}).waitFor();
  assert.equal(startBody.profile_id,1);assert.equal(startBody.recovery_url,'https://reader.example/inbox?email={email}');
  assert.equal(await dialog.locator('input[aria-label="Mật khẩu Outlook"]').inputValue(),'','password is cleared after submission');
  assert.equal(await dialog.getByRole('textbox',{name:'Email Outlook',exact:true}).isDisabled(),true);
  await dialog.getByRole('textbox',{name:'Mã xác minh Outlook',exact:true}).fill('123456');
  await dialog.getByRole('button',{name:'Gửi mã vào Outlook',exact:true}).click();
  await dialog.getByText('Đang xác minh',{exact:true}).waitFor();assert.deepEqual(manualBody,{code:'123456'});
  await dialog.getByRole('button',{name:'Dừng',exact:true}).click();
  await dialog.getByText('Đã dừng',{exact:true}).waitFor();
  await dialog.getByRole('button',{name:'Mở Outlook',exact:true}).click();assert.equal(opened,true);
  await dialog.getByRole('combobox',{name:'Chrome profile Outlook',exact:true}).selectOption('2');
  assert.equal(await dialog.getByRole('button',{name:'Đăng nhập & lấy mã',exact:true}).isDisabled(),true);
  for(const width of [960,600]){
   await page.setViewportSize({width,height:1000});
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
  }
  await dialog.getByRole('button',{name:'Close dialog',exact:true}).click();
  // The real DOM reader runs on intercepted Microsoft/provider pages, never live credentials.
  const dom=new Function('return ('+fs.readFileSync(path.join(root,'backend/app/services/outlook_dom.js'),'utf8')+')')();
  const landingPath='https://www.microsoft.com/en-us/microsoft-365/outlook/email-and-calendar-software-microsoft-outlook';
  const signInLink='https://go.microsoft.com/fwlink/p/?linkid=2125442&clcid=0x409&culture=en-us&country=us';
  await page.route('https://www.microsoft.com/**',route=>route.fulfill({body:'<h1>Your inbox, organized.</h1><a target="_blank" aria-label="Sign in to Outlook" href="'+signInLink+'">Sign in</a>',contentType:'text/html'}));
  await page.goto(landingPath);
  let landing=await page.evaluate(dom,{mode:'login'});
  assert.equal(landing.stage,'outlook_landing','the Outlook product page is a sign-in step, not an indefinite loading state');
  assert.equal(landing.signin_url,signInLink);
  await page.setContent('<h1>Outlook</h1><a href="https://reader.example/steal">Sign in to Outlook</a>');
  landing=await page.evaluate(dom,{mode:'login'});
  assert.equal(landing.stage,'outlook_landing');assert.equal(landing.signin_url,'','ignore off-site sign-in links');
  await page.goto('https://www.microsoft.com/en-us/store/');
  assert.equal((await page.evaluate(dom,{mode:'login'})).stage,'loading','a different Microsoft product page is not an Outlook landing page');
  let fixture='<body><input name="loginfmt"></body>';
  await page.route('https://login.live.com/**',route=>route.fulfill({body:fixture,contentType:'text/html'}));
  await page.goto('https://login.live.com/login.srf');
  assert.equal((await page.evaluate(dom,{mode:'login'})).stage,'email');
  const verifyEmail='<main><h1>Verify your email</h1><p>We will send a code to re*****@fviamail.work</p><input name="ProofConfirmation" type="email"><button id="send-code">Send code</button><a href="#password" id="password-alternative">Use your password</a></main><output id="sent">0</output><script>document.querySelector("#send-code").onclick=()=>document.querySelector("#sent").textContent="1";document.querySelector("#password-alternative").onclick=event=>{event.preventDefault();document.querySelector("main").innerHTML="<h1>Enter your password</h1><input type=\\"password\\" name=\\"passwd\\">"}</script>';
  await page.setContent(verifyEmail);
  let passwordChoice=await page.evaluate(dom,{mode:'login'});
  assert.equal(passwordChoice.stage,'use_password','choose password before sending a recovery code on the initial Verify your email screen');
  await page.locator(passwordChoice.selector).click();
  assert.equal((await page.evaluate(dom,{mode:'login'})).stage,'password');
  assert.equal(await page.locator('#sent').textContent(),'0','the password alternative must not send an email code');
  await page.setContent(verifyEmail);
  assert.equal((await page.evaluate(dom,{mode:'login',passwordEntered:true})).stage,'recovery','after password submission, continue any required verification without switching back');
  await page.setContent('<input name="otc"><button>Use your password</button>');
  assert.equal((await page.evaluate(dom,{mode:'login',passwordEntered:true})).stage,'code','never switch away from required MFA after password entry');
  await page.setContent('<input type="password"><button>Use your password</button>');
  assert.equal((await page.evaluate(dom,{mode:'login'})).stage,'password','fill an available password field directly');
  await page.setContent('<input name="ProofConfirmation"><button disabled>Use your password</button><a hidden>Use your password</a><a href="https://reader.example/">Use your password</a>');
  assert.equal((await page.evaluate(dom,{mode:'login'})).stage,'recovery','ignore disabled, hidden and off-site password alternatives');
  await page.setContent('<input name="ProofConfirmation"><span role="button">Utiliser votre mot de passe</span>');
  assert.equal((await page.evaluate(dom,{mode:'login'})).stage,'use_password');
  await page.setContent('<input type="password" name="passwd">');assert.equal((await page.evaluate(dom,{mode:'login'})).stage,'password');
  await page.setContent('<input name="ProofConfirmation">');assert.equal((await page.evaluate(dom,{mode:'login'})).stage,'recovery');
  await page.setContent('<input name="otc" autocomplete="one-time-code">');assert.equal((await page.evaluate(dom,{mode:'login'})).stage,'code');
  await page.setContent('<button>re*****@smvmail.com</button>');assert.equal((await page.evaluate(dom,{mode:'login',recoveryEmail:'recovery.sample@smvmail.com'})).stage,'choose_recovery');
  await page.setContent('<p>Your password is incorrect</p><input type="password">');assert.equal((await page.evaluate(dom,{mode:'login'})).stage,'rejected');
  await page.route('https://reader.example/**',route=>route.fulfill({body:'<main>Inbox recovery.sample@smvmail.com<article data-message-id="new">Microsoft account security code: 123456 <time datetime="2026-10-28T21:00:00Z">Just now</time></article></main>',contentType:'text/html'}));
  await page.goto('https://reader.example/inbox?email=recovery.sample%40smvmail.com');
  let data=await page.evaluate(dom,{mode:'reader',email:'recovery.sample@smvmail.com'});
  assert.equal(data.inbox,true);assert.equal(data.items.length,1);assert.equal(data.items[0].key,'id:new');
  await page.setContent('<main>Inbox recovery.sample@smvmail.com<article data-message-id="sender-link"><a href="mailto:account-security-noreply@accountprotection.microsoft.com">Microsoft account team</a><p>Security code 123456</p><a href="https://account.microsoft.com">Microsoft account verification</a></article></main>');
  data=await page.evaluate(dom,{mode:'reader',email:'recovery.sample@smvmail.com'});
  assert.equal(data.items.length,1);assert.equal(data.items[0].key,'id:sender-link','read the message, never click mailto or external verification links');
  await page.setContent('<main>Inbox recovery.sample@smvmail.com<article>Microsoft account security code: 123456 · 2h ago</article></main>');
  const firstAge=(await page.evaluate(dom,{mode:'reader',email:'recovery.sample@smvmail.com'})).items[0].key;
  await page.setContent('<main>Inbox recovery.sample@smvmail.com<article>Microsoft account security code: 123456 · 3h ago</article></main>');
  assert.equal((await page.evaluate(dom,{mode:'reader',email:'recovery.sample@smvmail.com'})).items[0].key,firstAge,'an aging old message is not a new message');
  await page.setContent('<main>Marketing demo inbox: Microsoft security code 123456</main>');
  data=await page.evaluate(dom,{mode:'reader',email:'other@smvmail.com'});assert.equal(data.inbox,false,'do not read codes from a marketing demo');
  await page.setContent('<input type="password" name="passwd">');assert.equal((await page.evaluate(dom,{mode:'login'})).stage,'loading','never fill login credentials on a reader host');
  await page.route('https://fviainboxes.com/**',route=>route.fulfill({body:'<input id="username" name="username" value="recovery.sample"><button aria-haspopup="listbox">fviamail.work</button><button>Get Email</button><main>Inbox <div class="cursor-pointer" data-message-id="fvia-message">Microsoft account security code: 123456</div></main>',contentType:'text/html'}));
  await page.goto('https://fviainboxes.com/');
  data=await page.evaluate(dom,{mode:'reader',email:'recovery.sample@fviamail.work'});
  assert.equal(data.inbox,false,'a public homepage is not the requested mailbox before Get Email');
  data=await page.evaluate(dom,{mode:'reader',email:'recovery.sample@fviamail.work',formAddress:'recovery.sample@fviamail.work'});
  assert.equal(data.inbox,true);assert.equal(data.items.length,1);assert.equal(data.items[0].key,'id:fvia-message');
  data=await page.evaluate(dom,{mode:'reader',email:'recovery.sample@dropinboxes.com',formAddress:'recovery.sample@dropinboxes.com'});
  assert.equal(data.inbox,false,'the selected FVia domain must match the requested recovery address');
  await page.setContent('<input id="username" name="username" value="recovery.sample"><button aria-haspopup="listbox">fviamail.work</button><main>Inbox Loading...</main>');
  data=await page.evaluate(dom,{mode:'reader',email:'recovery.sample@fviamail.work',formAddress:'recovery.sample@fviamail.work'});
  assert.equal(data.loading,true,'do not baseline a FVia inbox while messages are loading');
  await page.route('https://inboxes.com/**',route=>route.fulfill({body:'<h1>Don\'t give them your private email, use our inboxes</h1><table><tr><td>Inbox marketing demo recovery.sample@getnada.com Microsoft security code 123456</td></tr></table>',contentType:'text/html'}));
  await page.goto('https://inboxes.com/');
  data=await page.evaluate(dom,{mode:'reader',email:'recovery.sample@getnada.com'});
  assert.equal(data.inbox,false,'Inboxes marketing messages do not identify the current mailbox');
  await page.setContent('<h1>Use recovery.sample@getnada.com (click to copy.)</h1><table><tr data-message-id="inboxes-new"><td>Microsoft account team</td><td>Security code 123456</td><td>Just now</td></tr></table><p>Inbox</p>');
  data=await page.evaluate(dom,{mode:'reader',email:'recovery.sample@getnada.com'});
  assert.equal(data.inbox,true);assert.equal(data.items[0].key,'id:inboxes-new');
  data=await page.evaluate(dom,{mode:'reader',email:'sample@getnada.com'});
  assert.equal(data.inbox,false,'a partial local-part match is not the requested mailbox');
  await page.setContent('<h1>Use another@getnada.com (click to copy.)</h1><p>Inbox recovery.sample@getnada.com Microsoft security code 123456</p>');
  data=await page.evaluate(dom,{mode:'reader',email:'recovery.sample@getnada.com'});
  assert.equal(data.inbox,false,'only the active mailbox heading binds an Inboxes recipient');
  assert.deepEqual(errors,[]);
  console.log('PASS: Outlook form, masked paste/import, domain-only search, suggested/custom reader link, password clearing, manual code submission, cancellation/open actions, busy-profile guard, responsive layouts, Microsoft stages and recipient-bound inbox DOM extraction. Fixtures only; no real sign-in performed.');
 }finally{await browser.close()}
})().catch(error=>{console.error(error);process.exit(1)});
