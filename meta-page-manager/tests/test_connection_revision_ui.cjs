// Disposable offline browser: a second operator changes a connection while this form is open.
const {chromium}=require('../../chrome-manager/frontend/node_modules/playwright');
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const frontend=path.resolve(__dirname,'../frontend'),origin='http://127.0.0.1:8010';
(async()=>{
 const browser=await chromium.launch({channel:'chrome',headless:true});
 try{
  const context=await browser.newContext({offline:true}),page=await context.newPage(),writes=[],errors=[],outside=[];
  let connection={id:1,label:'Fixture connection',token_type:'page',app_id:'111',business_id:null,revision:1,status:'working',connected:true,has_token:true,page_count:0};
  await page.route('**/*',async route=>{
   const req=route.request(),url=new URL(req.url());
   if(url.origin!==origin){outside.push(url.origin);return route.abort()}
   if(url.pathname==='/auth/api/me')return route.fulfill({json:{user:{id:1,username:'fixture',role:'admin',must_change_password:false},csrf_token:'fixture-via-csrf'}});
   const name=url.pathname;
   if(name.startsWith('/api/')){
    let value;
    if(name==='/api/session')value={csrf_token:'fixture-csrf',app:{name:'Page Manager',version:'fixture',timezone:'UTC'}};
    else if(name==='/api/overview')value={stats:{pages:0,connections:1,working_connections:1,needs_attention:0}};
    else if(name==='/api/connections'&&req.method()==='GET')value={items:[connection]};
    else if(name==='/api/pages'||name==='/api/events')value={items:[]};
    else if(name==='/api/connections/1'&&req.method()==='PUT'){
     const body=req.postDataJSON();writes.push(body);assert.equal(req.headers()['x-mpm-csrf'],'fixture-csrf');assert.equal(req.headers()['x-via-csrf'],'fixture-via-csrf');
     if(body.revision!==connection.revision)return route.fulfill({status:409,json:{detail:'Kết nối đã thay đổi ở phiên khác. Đọc lại trước khi lưu.',code:'connection_revision_changed'}});
     connection={...connection,...body,revision:connection.revision+1};delete connection.token;value=connection;
    }else return route.fulfill({status:404,json:{detail:'Fixture API unavailable'}});
    return route.fulfill({json:value});
   }
   if(name==='/')return route.fulfill({path:path.join(frontend,'index.html'),contentType:'text/html'});
   if(name.startsWith('/static/')){
    const file=path.resolve(frontend,name.slice(8));
    if(!file.startsWith(frontend+path.sep)||!fs.existsSync(file))return route.abort();
    return route.fulfill({path:file,contentType:file.endsWith('.js')?'application/javascript':file.endsWith('.woff2')?'font/woff2':'text/css'});
   }
   return route.abort();
  });
  page.on('pageerror',error=>errors.push(error.message));
  await page.goto(origin+'/');
  await page.getByRole('button',{name:'Settings',exact:true}).click();
  await page.getByRole('tab',{name:'Kết nối token',exact:true}).click();
  await page.getByRole('button',{name:'Cập nhật token',exact:true}).click();
  const form=page.getByRole('dialog',{name:'Cập nhật kết nối',exact:true});
  await form.locator('input[name="label"]').fill('My stale edit');
  await form.locator('input[name="token"]').fill('FixturePrivateReplacement82');
  connection={...connection,revision:2,app_id:'222'}; // Another operator saved after this form opened.
  await form.getByRole('button',{name:'Lưu cập nhật',exact:true}).click();
  await form.getByRole('button',{name:'Đóng và đọc lại kết nối',exact:true}).waitFor();
  assert.equal(writes.length,1);assert.equal(writes[0].revision,1);
  assert.equal(await form.locator('input[name="token"]').inputValue(),'');
  assert.equal(await form.getByRole('button',{name:'Lưu cập nhật',exact:true}).isDisabled(),true);
  await form.getByRole('button',{name:'Đóng và đọc lại kết nối',exact:true}).click();
  await form.waitFor({state:'hidden'});
  await page.getByText('App ID: 222',{exact:true}).waitFor();
  await page.getByRole('button',{name:'Cập nhật token',exact:true}).click();
  assert.equal(await form.locator('input[name="app_id"]').inputValue(),'222');
  await form.locator('input[name="label"]').fill('Fresh edit');
  await form.getByRole('button',{name:'Lưu cập nhật',exact:true}).click();
  await form.waitFor({state:'hidden'});
  assert.equal(writes.length,2);assert.equal(writes[1].revision,2);assert.equal(writes[1].app_id,'222');
  assert.equal(writes[1].token,undefined);assert.equal(connection.revision,3);
  assert.deepEqual(errors,[]);assert.deepEqual(outside,[]);
  console.log('PASS: stale connection revision rejected once; secret cleared; explicit reload preserves newer scope; fresh save sends new revision. Offline synthetic browser only.');
 }finally{await browser.close()}
})().catch(error=>{console.error(error);process.exit(1)});
