// Offline synthetic assets/API only; never reads tokens or contacts Meta.
const {chromium}=require('../../chrome-manager/frontend/node_modules/playwright');
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const root=path.resolve(__dirname,'..'),frontend=path.join(root,'frontend'),mounted=process.argv.includes('--mounted'),base=mounted?'/pages':'',origin=mounted?'http://127.0.0.1:8000':'http://127.0.0.1:8010';
const png=Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aZ1sAAAAASUVORK5CYII=','base64');
(async()=>{
 const browser=await chromium.launch({channel:'chrome',headless:true});
 try{
  const context=await browser.newContext({offline:true,viewport:{width:1450,height:1000}}),page=await context.newPage(),errors=[],outside=[],avatarRequests=[],writes=[],seen=[];
  const source={id:1,label:'Fixture source',status:'working',sources:['assigned_pages'],has_page_token:true};
  const item=(id,name,avatar_url)=>({id,name,...(avatar_url!==undefined?{avatar_url}:{}),status:'active',category:'Retail',connections:[source],details:{},posts_today:null,posts_today_complete:false});
  const pages=[item('555','Amber Place','/api/pages/555/avatar'),item('556','Fallback Page','/api/pages/556/avatar'),item('557','Legacy Page'),item('558','Remote Page','https://cdn.invalid/remote.png'),item('559','Wrong Page','/api/pages/555/avatar'),item('560','Secret Query','/api/pages/560/avatar?access_token=FixtureSecretToken82')];
  await page.route('**/*',async route=>{
   const req=route.request(),url=new URL(req.url());
   if(url.origin===origin&&url.pathname==='/auth/api/me')return route.fulfill({json:{user:{id:1,username:'fixture',role:'admin',must_change_password:false},csrf_token:'fixture-via-csrf'}});
   if(url.origin===origin&&/^(?:\/pages)?\/api\//.test(url.pathname)&&!['GET','HEAD','OPTIONS'].includes(req.method()))assert.equal(req.headers()['x-via-csrf'],'fixture-via-csrf');
seen.push(url.pathname);
   if(url.origin!==origin||!url.pathname.startsWith(base+'/')){outside.push(req.url());return route.abort()}
   const p=url.pathname.slice(base.length);
   if(p.startsWith('/api/')){
    if(req.method()!=='GET')writes.push(p);
    if(/^\/api\/pages\/\d+\/avatar$/.test(p)){
     avatarRequests.push(req.url());assert.equal(req.method(),'GET');assert.equal(url.search,'');assert.equal(req.headers().authorization,undefined);
     return p==='/api/pages/555/avatar'?route.fulfill({contentType:'image/png',body:png}):route.fulfill({status:404,json:{detail:'Chưa có ảnh đại diện.'}});
    }
    if(p==='/api/session')return route.fulfill({json:{csrf_token:'fixture-avatar-csrf',app:{name:'Page Manager',timezone:'UTC',graph_version:'v26.0',page_editing:true}}});
    if(p==='/api/overview')return route.fulfill({json:{stats:{pages:pages.length},updated_at:new Date().toISOString()}});
    if(p==='/api/connections')return route.fulfill({json:{items:[{...source,token_type:'system_user',connected:true,has_token:true}]}});
    if(p==='/api/pages')return route.fulfill({json:{items:pages}});
    if(p==='/api/events')return route.fulfill({json:{items:[]}});
    if(/^\/api\/pages\/\d+$/.test(p))return route.fulfill({json:{page:pages.find(item=>item.id===p.split('/')[3]),posts:[],posts_has_more:false,posts_checked_at:null,posts_error:null}});
    return route.fulfill({status:404,json:{detail:'Unknown offline API'}});
   }
   if(p==='/')return route.fulfill({path:path.join(frontend,'index.html'),contentType:'text/html'});
   if(p.startsWith('/static/')){
    const file=path.resolve(frontend,p.slice(8));if(!file.startsWith(frontend+path.sep)||!fs.existsSync(file))return route.abort();
    return route.fulfill({path:file,contentType:file.endsWith('.js')?'application/javascript':file.endsWith('.woff2')?'font/woff2':'text/css'});
   }
   return route.abort();
  });
  page.on('pageerror',error=>errors.push(error.message));
  await page.goto(origin+base+'/');
  const amber=page.getByRole('row').filter({has:page.getByRole('button',{name:'Xem chi tiết Page Amber Place',exact:true})});
  const photo=amber.getByRole('img',{name:'Ảnh đại diện Page Amber Place',exact:true});
  await photo.locator('img').waitFor();await page.waitForFunction(()=>document.querySelector('.pages-table .avatar.has-photo'));
  assert.equal(await photo.locator('img').getAttribute('src'),base+'/api/pages/555/avatar');
  assert.equal(await photo.locator('img').getAttribute('loading'),'lazy');assert.equal(await photo.locator('img').getAttribute('referrerpolicy'),'no-referrer');
  assert.equal(await photo.locator('.avatar-initials').isVisible(),false);
  const fallback=page.getByRole('row').filter({has:page.getByRole('button',{name:'Xem chi tiết Page Fallback Page',exact:true})}).getByRole('img',{name:'Ảnh đại diện Page Fallback Page',exact:true});
  await fallback.scrollIntoViewIfNeeded();await page.waitForFunction(()=>!document.querySelectorAll('.pages-table tbody tr')[1]?.querySelector('.avatar img'));
  assert.equal(await fallback.locator('.avatar-initials').innerText(),'FP');assert.equal(await fallback.locator('.avatar-initials').isVisible(),true);
  for(const name of ['Legacy Page','Remote Page','Wrong Page','Secret Query']){
   const row=page.getByRole('row').filter({has:page.getByRole('button',{name:'Xem chi tiết Page '+name,exact:true})});assert.equal(await row.locator('.avatar img').count(),0);
  }
  await page.getByRole('button',{name:'Xem chi tiết Page Amber Place',exact:true}).click();
  const drawer=page.getByRole('dialog',{name:'Chi tiết Page',exact:true});
  const detailPhoto=drawer.getByRole('img',{name:'Ảnh đại diện Page Amber Place',exact:true});
  await page.waitForFunction(()=>document.querySelector('#page-dialog .avatar.has-photo'));
  assert.equal(await detailPhoto.locator('img').getAttribute('src'),base+'/api/pages/555/avatar');
  await page.setViewportSize({width:390,height:844});
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
  const box=await detailPhoto.boundingBox();assert.ok(box&&box.width>0&&box.width===box.height);
  // The reusable two-argument helper stays usable, and malicious metadata cannot
  // turn avatar rendering into a remote fetch or leak a query credential.
  const guards=await page.evaluate(async()=>{
   const {avatar}=await import('./static/ui.js');
   const values=[undefined,'https://cdn.invalid/image.png','//cdn.invalid/image.png','/api/pages/556/avatar','/api/pages/555/avatar?access_token=secret','/api/pages/555/avatar#secret','/api/pages/555/avatar/','/api/pages/555%2favatar'];
   return values.map(url=>{const node=avatar('Fixture Page','555',url);return {images:node.querySelectorAll('img').length,text:node.textContent,label:node.getAttribute('aria-label')}});
  });
  assert.ok(guards.every(value=>value.images===0&&value.text==='FP'&&value.label==='Ảnh đại diện Page Fixture Page'));
  assert.ok(avatarRequests.length>=2&&avatarRequests.every(url=>new URL(url).pathname.startsWith(base+'/api/pages/')));
  assert.ok(avatarRequests.every(url=>/\/(555|556)\/avatar$/.test(new URL(url).pathname)));
  if(mounted)assert.equal(seen.some(p=>p.startsWith('/api/')),false);
  assert.deepEqual(writes,[]);assert.deepEqual(outside,[]);assert.deepEqual(errors,[]);
  console.log('PASS: '+(mounted?'mounted':'standalone')+' Page avatar list/detail, lazy same-origin images, accessible error/legacy fallback, strict Page-path validation and no token/remote requests. Offline only.');
 }finally{await browser.close()}
})().catch(error=>{console.error(error);process.exit(1)});
