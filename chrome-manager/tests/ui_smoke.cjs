// Real Chrome UI/layout test. API calls are aborted: this does NOT substitute for backend tests.
const {chromium}=require('../frontend/node_modules/playwright');
const fs=require('node:fs');
const path=require('node:path');
const assert=require('node:assert/strict');
(async()=>{
 const root=path.resolve(__dirname,'..'),dist=path.resolve(process.env.CM_UI_DIST||path.join(root,'frontend','dist')),output=path.join(root,'.runtime','ui');
 fs.mkdirSync(output,{recursive:true});
 const browser=await chromium.launch({channel:'chrome',headless:true});
 try{
  const page=await browser.newPage({offline:true,serviceWorkers:'block',viewport:{width:1536,height:1000}});
  require('./workspace_fixture.cjs').installIdentityFixture(page);
  const errors=[];
  let importReply=null;
  page.on('pageerror',error=>errors.push(error.message));
  const openSettings=async section=>{await page.locator('.sidebar').getByRole('button',{name:'Settings',exact:true}).click();await page.getByRole('tab',{name:section,exact:true}).click()};
  await page.route('http://127.0.0.1:8000/**',async route=>{
   const url=new URL(route.request().url());
   if(url.pathname==='/api/session')return route.fulfill({json:{ok:true,rest_api_version:1}});
   if(url.pathname==='/api/v1/proxies/import'&&importReply)return route.fulfill({json:importReply});
   if(url.pathname.startsWith('/api/'))return route.abort('connectionfailed');
   const file=path.resolve(dist,'.'+(url.pathname==='/'?'/index.html':url.pathname));
   if(!file.startsWith(dist+path.sep)||!fs.existsSync(file))return route.abort();
   return route.fulfill({path:file,contentType:file.endsWith('.js')?'application/javascript':file.endsWith('.css')?'text/css':file.endsWith('.woff2')?'font/woff2':'text/html'});
  });
  await page.goto('http://127.0.0.1:8000/');
  await page.getByRole('heading',{name:/^Chrome profiles/}).waitFor();
  const fontLoaded=await page.evaluate(async()=>{
    const faces=await document.fonts.load('400 14px Geist','Tiếng Việt ă â ê ô ơ ư đ Ắ ệ Ỡ');
    await document.fonts.ready;
    return faces.length>0&&document.fonts.check('400 14px Geist','Tiếng Việt');
  });
  assert.equal(fontLoaded,true,'Bundled Geist must render Vietnamese text');
  await page.getByRole('button',{name:'Collapse sidebar',exact:true}).click();
  assert.equal(await page.locator('.app-shell.sidebar-collapsed').count(),1);
  await page.getByRole('button',{name:'Expand sidebar',exact:true}).click();


  await page.screenshot({path:path.join(output,'dashboard.png'),fullPage:true});
  await page.getByRole('button',{name:'Dashboard',exact:true}).click();
  assert.equal(await page.getByRole('navigation',{name:'profiles pagination'}).count(),1);
  await page.getByRole('button',{name:'New profile',exact:true}).first().click();
  await page.getByRole('dialog').waitFor();
  await page.getByPlaceholder('e.g. QA Workspace').fill('UI smoke profile');
  await page.getByRole('button',{name:'Cancel',exact:true}).click();
  assert.equal(await page.getByRole('dialog').count(),0);
  await page.getByRole('button',{name:'Import Proxies',exact:true}).click();
  await page.getByRole('dialog').waitFor();
  const txt=Array.from({length:100},(_,i)=>'192.0.2.10:'+(10000+i)+':test_user_'+i+':test_password_'+i).join('\r\n');
  await page.locator('input[type=file]').setInputFiles({name:'proxies.txt',mimeType:'text/plain',buffer:Buffer.from(txt)});
  await page.waitForFunction(()=>document.querySelector('textarea')?.value.split('\n').length===100);
  const requestPromise=page.waitForRequest(request=>request.url().endsWith('/api/v1/proxies/import')&&request.method()==='POST');
  await page.getByRole('button',{name:'Import proxies',exact:true}).last().click();
  const request=await requestPromise;
  const payload=JSON.parse(request.postData());
  assert.equal(payload.content,txt);
  assert.equal(payload.assign,true);
  assert.equal(payload.check_after_import,true);
  await page.getByRole('button',{name:'Close dialog'}).click();
  importReply={created:1,assigned:1,duplicates:0,proxy_ids:[31],checking:[31],errors:[]};
  await page.getByRole('button',{name:'Import Proxies',exact:true}).click();
  await page.getByRole('dialog').locator('textarea').fill('192.0.2.31:10031');
  await page.getByRole('dialog').getByRole('button',{name:'Import proxies',exact:true}).click();
  await page.getByRole('dialog').waitFor({state:'hidden'});
  await page.getByText('1 imported · 1 assigned · 0 duplicates · 1 checks queued',{exact:true}).waitFor();
  await page.screenshot({path:path.join(output,'proxy-import-auto-close.png'),fullPage:false});
  importReply={created:1,assigned:1,duplicates:0,proxy_ids:[31],checking:[31],errors:[{line:2,message:'Invalid proxy'}]};
  await page.getByRole('button',{name:'Import Proxies',exact:true}).click();
  await page.getByRole('dialog').locator('textarea').fill('192.0.2.31:10031\nnot valid proxy');
  await page.getByRole('dialog').getByRole('button',{name:'Import proxies',exact:true}).click();
  await page.getByRole('dialog').locator('pre').filter({hasText:'Rejected lines: 2'}).waitFor();
  assert.equal(await page.getByRole('dialog').locator('textarea').inputValue(),'192.0.2.31:10031\nnot valid proxy','keep invalid rows visible for correction');
  await page.getByRole('dialog').getByRole('button',{name:'Close',exact:true}).click();
  importReply=null;
  await openSettings('Automation Tasks');
  await page.getByRole('button',{name:'Create task',exact:true}).first().click();
  await page.getByRole('dialog').waitFor();
  await page.getByRole('button',{name:'Add action',exact:true}).click();
  assert.equal(await page.locator('.step').count(),6);
  await page.getByRole('combobox',{name:'Action 6',exact:true}).selectOption('scroll');
  await page.getByRole('spinbutton',{name:'Scroll pixels'}).fill('500');
  await page.screenshot({path:path.join(output,'task-builder.png'),fullPage:true});
  await page.getByRole('button',{name:'Cancel',exact:true}).click();
  await page.getByRole('button',{name:'Dashboard',exact:true}).click();
  await openSettings('Dữ liệu & công cụ');
  await page.getByRole('button',{name:'Authenticator',exact:true}).click();
  await page.getByRole('dialog').waitFor();
  assert.equal(await page.getByText("Only this Manager's profiles",{exact:true}).count(),1);
  assert.equal(await page.getByRole('button',{name:'Open Web Store for selected (0)',exact:true}).isDisabled(),true);
  await page.screenshot({path:path.join(output,'authenticator-setup-dialog.png'),fullPage:true});
  await page.getByRole('button',{name:'Close dialog'}).click();
  await page.getByRole('button',{name:'Dashboard',exact:true}).click();
  for(const width of [960,600]){
   await page.setViewportSize({width,height:900});
   const overflow=await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth);
   assert.equal(overflow,false,'Viewport must not overflow at '+width);
   await page.screenshot({path:path.join(output,'dashboard-'+width+'.png'),fullPage:true});
  }
  assert.deepEqual(errors,[]);
  console.log('PASS: real Chrome render, profile/import/task dialogs, TXT file upload of 100 proxy rows, action builder, responsive layout, bundled Vietnamese Geist font, sidebar collapse, pagination controls and per-profile Authenticator setup dialog; no JavaScript errors. Backend connectivity is not tested.');
 }finally{await browser.close()}
})().catch(error=>{console.error(error);process.exit(1)});
