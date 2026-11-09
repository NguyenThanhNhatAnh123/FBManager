// Disposable offline fixture only; no live token, Page, DB or Meta writes.
const {chromium}=require('../../chrome-manager/frontend/node_modules/playwright');
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const root=path.resolve(__dirname,'..'),frontend=path.join(root,'frontend'),base=process.argv.includes('--mounted')?'/pages':'',origin=base?'http://127.0.0.1:8000':'http://127.0.0.1:8010';
(async()=>{
 const browser=await chromium.launch({channel:'chrome',headless:true});
 try{
  const context=await browser.newContext({offline:true,viewport:{width:1450,height:1000}}),page=await context.newPage(),writes=[],outside=[],errors=[];
  let unknown=false,outcome='accepted';
  const sources=[{id:1,label:'Fixture source',status:'working',has_page_token:true},{id:2,label:'Legacy source',status:'working',has_page_token:true}];
  const item={id:'555',name:'Fixture Page',category:'Retail',connections:sources,status:'active',posts_today:null,posts_today_complete:false,details:{website:'https://old.example',is_always_open:false,location:{street:'1942 Broadway St. STE 314C',city:'Boulder',state:'CO',zip:'80302',country:'US'}}};
  const options=cid=>({page_id:'555',connection_id:cid,current:{bio:'Current introduction',bio_field:'bio',location:null,category_ids:null,website:unknown?null:'https://old.example',is_always_open:unknown?null:true},supported_fields:cid===1?['bio','website','is_always_open']:['bio'],limits:{bio_max_length:101}});
  await page.route('**/*',async route=>{
   const req=route.request(),url=new URL(req.url());
   if(url.origin===origin&&url.pathname==='/auth/api/me')return route.fulfill({json:{user:{id:1,username:'fixture',role:'admin',must_change_password:false},csrf_token:'fixture-via-csrf'}});
   if(url.origin===origin&&/^(?:\/pages)?\/api\//.test(url.pathname)&&!['GET','HEAD','OPTIONS'].includes(req.method()))assert.equal(req.headers()['x-via-csrf'],'fixture-via-csrf');

   if(url.origin!==origin||!url.pathname.startsWith(base+'/')){outside.push(req.url());return route.abort()}
   const p=url.pathname.slice(base.length),body=req.postData()?req.postDataJSON():null;
   if(p.startsWith('/api/')){
    if(req.method()!=='GET')assert.equal(req.headers()['x-mpm-csrf'],'metadata-fixture-csrf');
    if(p==='/api/session')return route.fulfill({json:{csrf_token:'metadata-fixture-csrf',app:{name:'Page Manager',timezone:'UTC',page_editing:true}}});
    if(p==='/api/overview')return route.fulfill({json:{stats:{pages:1},updated_at:new Date().toISOString()}});
    if(p==='/api/connections')return route.fulfill({json:{items:sources.map(value=>({...value,connected:true,has_token:true,token_type:'system_user'}))}});
    if(p==='/api/pages')return route.fulfill({json:{items:[item]}});
    if(p==='/api/events')return route.fulfill({json:{items:[]}});
    if(p==='/api/pages/555')return route.fulfill({json:{page:item,posts:[],posts_has_more:false,posts_checked_at:null,posts_error:null}});
    if(p==='/api/pages/555/edit-options')return route.fulfill({json:options(body.connection_id)});
    if(p==='/api/pages/555/edit'){
     writes.push({...body});assert.equal(body.connection_id,1);assert.match(body.request_id,/^[0-9a-f-]{36}$/);
     return route.fulfill({json:{ok:outcome==='accepted',outcome,message:'Fixture acknowledged',requires_refresh:true}});
    }
    return route.fulfill({status:404,json:{detail:'Unknown metadata fixture route'}});
   }
   if(p==='/')return route.fulfill({path:path.join(frontend,'index.html'),contentType:'text/html'});
   if(p.startsWith('/static/')){const file=path.resolve(frontend,p.slice(8));if(!file.startsWith(frontend+path.sep)||!fs.existsSync(file))return route.abort();return route.fulfill({path:file,contentType:file.endsWith('.js')?'application/javascript':file.endsWith('.woff2')?'font/woff2':'text/css'})}
   return route.abort();
  });
  page.on('pageerror',error=>errors.push(error.message));await page.goto(origin+base+'/');
  const drawer=page.getByRole('dialog',{name:'Chi tiết Page',exact:true}),editor=page.locator('#page-edit-dialog');
  const toggle=label=>editor.getByRole('checkbox',{name:'Thay đổi '+label,exact:true});
  const hours=()=>editor.getByRole('checkbox',{name:'Luôn mở cửa',exact:true});
  async function openEditor(cid='1'){
   await page.getByRole('button',{name:'Xem chi tiết Page Fixture Page',exact:true}).click();
   await drawer.getByRole('button',{name:'Chỉnh sửa Page',exact:true}).click();
   await editor.getByRole('combobox',{name:'Nguồn chỉnh sửa Page',exact:true}).selectOption(cid);
   await editor.getByRole('checkbox',{name:'Thay đổi Bio / Giới thiệu ngắn',exact:true}).waitFor();
   if(cid==='1')await toggle('Website').waitFor();
  }
  async function close(){await editor.getByRole('button',{name:'Đóng chỉnh sửa Page',exact:true}).click();await drawer.getByRole('button',{name:'Đóng chi tiết Page',exact:true}).click()}
  async function review(){await editor.getByRole('button',{name:'Xem lại thay đổi',exact:true}).click()}
  async function save(){await editor.getByRole('button',{name:'Lưu lên Page',exact:true}).evaluate(button=>{button.click();button.click()});await editor.getByRole('heading',{name:'Kết quả gửi thay đổi',exact:true}).waitFor()}

  await openEditor();
  await drawer.getByText('1942 Broadway St. STE 314C, Boulder, CO, US, 80302',{exact:true}).waitFor();
  await drawer.getByText('Không bật luôn mở cửa',{exact:true}).waitFor();
  assert.equal(await editor.getByRole('textbox',{name:'Website mới',exact:true}).isDisabled(),true);assert.equal(await hours().isDisabled(),true);assert.equal(await hours().isChecked(),true);
  await toggle('Website').check();await editor.getByRole('textbox',{name:'Website mới',exact:true}).fill('http://unsafe.example');await review();
  await editor.getByText('Website phải là địa chỉ HTTPS hợp lệ, không kèm thông tin đăng nhập.',{exact:true}).waitFor();assert.equal(writes.length,0);
  await editor.getByRole('textbox',{name:'Website mới',exact:true}).fill('https://new.example');
  await toggle('Giờ mở cửa').check();await hours().uncheck();await review();
  await editor.getByText('https://old.example',{exact:true}).waitFor();await editor.getByText('https://new.example',{exact:true}).waitFor();
  await editor.getByText('Luôn mở cửa',{exact:true}).waitFor();await editor.getByText('Không bật luôn mở cửa',{exact:true}).waitFor();
  await editor.getByText('Nguồn: Fixture source · #1',{exact:true}).waitFor();assert.equal(writes.length,0);
  await page.setViewportSize({width:390,height:844});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);await page.setViewportSize({width:1450,height:1000});
  await save();assert.equal(writes.length,1);assert.deepEqual(Object.keys(writes[0]).sort(),['connection_id','is_always_open','request_id','website']);assert.equal(writes[0].website,'https://new.example');assert.equal(writes[0].is_always_open,false);await close();

  unknown=true;await openEditor();assert.equal(await hours().evaluate(node=>node.indeterminate),true);
  await toggle('Website').check();await toggle('Bio / Giới thiệu ngắn').check();await editor.getByRole('textbox',{name:'Bio mới',exact:true}).fill('Explicit introduction');await review();await save();
  assert.deepEqual(Object.keys(writes.at(-1)).sort(),['bio','connection_id','request_id'],'Unknown empty website and untouched hours are not sent');await close();
  await openEditor();await toggle('Giờ mở cửa').check();await review();
  await editor.getByText('Chọn rõ trạng thái Luôn mở cửa trước khi xem lại thay đổi.',{exact:true}).waitFor();assert.equal(writes.length,2);
  await hours().check();await hours().uncheck();await toggle('Website').check();await editor.getByRole('textbox',{name:'Website mới',exact:true}).fill('https://explicit.example');await review();
  assert.equal(await editor.getByText('Chưa đọc được dữ liệu hiện tại',{exact:true}).count(),2);await save();
  assert.equal(writes.at(-1).is_always_open,false);assert.equal(writes.at(-1).website,'https://explicit.example');await close();

  unknown=false;await openEditor();await toggle('Website').check();await editor.getByRole('textbox',{name:'Website mới',exact:true}).fill('');await review();await save();
  assert.equal(writes.at(-1).website,'');assert.equal(Object.hasOwn(writes.at(-1),'is_always_open'),false,'Known website clearing never changes hours');await close();
  await openEditor();await toggle('Website').check();await editor.getByRole('textbox',{name:'Website mới',exact:true}).fill('https://discard.example');
  await editor.getByRole('combobox',{name:'Nguồn chỉnh sửa Page',exact:true}).selectOption('2');
  await editor.getByRole('checkbox',{name:'Thay đổi Bio / Giới thiệu ngắn',exact:true}).waitFor();
  assert.equal(await toggle('Website').count(),0);assert.equal(await toggle('Giờ mở cửa').count(),0,'Old server/source capabilities do not show new controls');await close();
  for(const value of ['partial','unknown','failed']){
   outcome=value;const before=writes.length;await openEditor();await toggle('Giờ mở cửa').check();await hours().uncheck();await review();await save();
   await page.waitForTimeout(50);assert.equal(writes.length,before+1);assert.equal(await editor.getByRole('button',{name:'Lưu lên Page',exact:true}).count(),0,'No re-submit after '+value);await close();
  }
  assert.equal(new Set(writes.map(value=>value.request_id)).size,writes.length);assert.deepEqual(outside,[]);assert.deepEqual(errors,[]);
  assert.equal(await page.evaluate(()=>localStorage.length+sessionStorage.length),0);
  console.log('PASS: '+(base?'mounted':'standalone')+' metadata Website/hours opt-in, HTTPS validation, exact review/source, explicit false vs unknown, known clearing, capability hiding, stop/no replay and drawer state. Offline only.');
 }finally{await browser.close()}
})().catch(error=>{console.error(error);process.exit(1)});
