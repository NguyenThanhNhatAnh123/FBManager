// All resources and API calls are intercepted; no real CLI or service is used.
const {chromium}=require('../../chrome-manager/frontend/node_modules/playwright');
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const frontend=path.resolve(__dirname,'../frontend'),origin='http://127.0.0.1:8000';
const row=(authenticated,version)=>({installed:true,authenticated,version,status:'cli_ready',checked_at:new Date().toISOString(),expires_at:new Date(Date.now()+300000).toISOString(),image_generation:{configured:true,verified:false},production_verified:false});
const value=()=>({primary:'codex',backup:'claude',providers:{codex:row(true,'0.163.0'),claude:{...row(null,'2.1.302'),status:'not_checked',image_generation:{configured:null,verified:false}}},fallback:{mode:'manual_review',automatic:false}});
(async()=>{
 const browser=await chromium.launch({channel:'chrome',headless:true});
 try{
  const context=await browser.newContext({offline:true,serviceWorkers:'block',viewport:{width:1200,height:1000}}),page=await context.newPage();
  const writes=[],outside=[],errors=[];let release=null,fail=false;
  await page.route('**/*',async route=>{
   const request=route.request(),url=new URL(request.url());
   if(url.origin===origin&&url.pathname==='/auth/api/me')return route.fulfill({json:{user:{id:1,username:'fixture',role:'admin',must_change_password:false},csrf_token:'fixture-via-csrf'}});
   if(url.origin===origin&&/^(?:\/pages)?\/api\//.test(url.pathname)&&!['GET','HEAD','OPTIONS'].includes(request.method()))assert.equal(request.headers()['x-via-csrf'],'fixture-via-csrf');

   if(url.origin!==origin){outside.push(url.href);return route.abort()}
   if(url.pathname==='/')return route.fulfill({contentType:'text/html',body:'<!doctype html><html><body><main></main><script type="module">import {createProducerConnections} from "/static/producer-connections.js";window.panel=createProducerConnections();document.querySelector("main").append(panel.node)</script></body></html>'});
   if(url.pathname.startsWith('/static/')){
    const file=path.resolve(frontend,url.pathname.slice(8));assert.ok(file.startsWith(frontend+path.sep));assert.ok(fs.existsSync(file));
    return route.fulfill({path:file,contentType:file.endsWith('.js')?'application/javascript':'text/css'});
   }
   if(url.pathname==='/api/session')return route.fulfill({json:{csrf_token:'fixture-producer-ui',app:{}}});
   if(url.pathname==='/api/idea-pipeline/producers')return route.fulfill({json:value()});
   if(url.pathname==='/api/idea-pipeline/producers/check'){
    assert.equal(request.method(),'POST');assert.equal(request.headers()['x-mpm-csrf'],'fixture-producer-ui');writes.push(request.postDataJSON());
    await new Promise(resolve=>{release=resolve});
    if(fail)return route.fulfill({status:503,json:{detail:'Chưa hoàn tất kiểm tra kết nối.'}});
    const result=value();result.providers.claude={...row(false,'2.1.302'),status:'not_authenticated',image_generation:{configured:null,verified:false}};
    return route.fulfill({json:result});
   }
   throw Error('Unexpected fixture request '+request.method()+' '+url.pathname);
  });
  page.on('pageerror',error=>errors.push(error.message));
  await page.goto(origin+'/');await page.getByRole('heading',{name:'Kết nối AI producer',exact:true}).waitFor();
  assert.equal(writes.length,0);
  await page.getByRole('button',{name:'Đọc trạng thái producer',exact:true}).click();
  const codex=page.locator('[data-provider="codex"]'),claude=page.locator('[data-provider="claude"]');
  await codex.getByText('CLI đã đăng nhập',{exact:true}).waitFor();
  assert.match(await codex.innerText(),/0\.163\.0/);assert.match(await codex.innerText(),/Chờ kiểm chứng tạo ảnh và QA/);
  assert.match(await claude.innerText(),/Chưa xác minh/);assert.equal(writes.length,0);
  await page.getByRole('button',{name:'Kiểm tra cả hai',exact:true}).evaluate(node=>{node.click();node.click()});
  await page.getByRole('button',{name:'Đang kiểm tra…',exact:true}).waitFor();
  assert.equal(await page.getByRole('button',{name:'Kiểm tra kết nối Codex',exact:true}).isDisabled(),true);
  while(!release)await page.waitForTimeout(10);assert.deepEqual(writes,[{provider:'all'}]);release();release=null;
  await claude.getByText('Cần đăng nhập',{exact:true}).first().waitFor();
  assert.match(await codex.innerText(),/Chờ kiểm chứng tạo ảnh và QA/);
  await page.evaluate(value=>{value.providers.codex.expires_at='2001-01-01T00:00:00Z';panel.adopt(value)},value());
  await codex.getByText('Cần kiểm tra lại',{exact:true}).waitFor();
  assert.equal(await codex.getByText('Đã đăng nhập',{exact:true}).count(),0,'Expired cache cannot claim an authenticated session');
  fail=true;await page.getByRole('button',{name:'Kiểm tra kết nối Codex',exact:true}).click();
  while(!release)await page.waitForTimeout(10);release();release=null;
  await page.getByRole('alert').getByText('Chưa hoàn tất kiểm tra kết nối.',{exact:true}).waitFor();
  assert.deepEqual(writes,[{provider:'all'},{provider:'codex'}]);assert.equal(await page.getByRole('button',{name:'Kiểm tra kết nối Codex',exact:true}).isEnabled(),true);
  assert.equal(await page.evaluate(()=>localStorage.length+sessionStorage.length),0);
  assert.deepEqual(outside,[]);assert.deepEqual(errors,[]);
  console.log('Producer connection UI: cache, expiry, singleflight, auth distinction and retry checks passed.');
 }finally{await browser.close()}
})().catch(error=>{console.error(error);process.exitCode=1});
