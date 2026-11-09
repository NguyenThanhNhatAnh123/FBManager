// Disposable offline fixtures only. No token, live database, Meta request or real Page write.
const {chromium}=require('../../chrome-manager/frontend/node_modules/playwright');
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const root=path.resolve(__dirname,'..'),frontend=path.join(root,'frontend'),mount=process.argv.includes('--mounted')?'/pages':'',origin=mount?'http://127.0.0.1:8000':'http://127.0.0.1:8010';
const tinyPng=Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aZ1sAAAAASUVORK5CYII=','base64');
const stamp=new Date().toISOString();
(async()=>{
 const browser=await chromium.launch({channel:'chrome',headless:true});
 try{
  const context=await browser.newContext({offline:true,viewport:{width:1450,height:1000}}),page=await context.newPage(),errors=[],outside=[],mutations=[],optionReads=[],refreshes=[];
  let mode='accepted',session=0,delayOptions=false,optionsRelease=null,categoryRelease=null,delayCategory=false,unknownCurrent=false,editingEnabled=true;
  const csrf=()=>`editor-csrf-${session}`;
  const sources=[{id:1,label:'Nguồn chính',status:'working',sources:['me/accounts'],tasks:['MANAGE'],has_page_token:true},{id:2,label:'Nguồn phụ',status:'working',sources:['me/accounts'],tasks:[],has_page_token:true}];
  const pages=[{id:'90000001',name:'Page A · Fixture',category:'Thiết kế',link:null,status:'active',connections:sources,details:{bio:'Bio A hiện tại'},checked_at:stamp,error:null,posts_today:null,posts_today_complete:false,posts_checked_at:null},{id:'90000002',name:'Page B · Fixture',category:'Cửa hàng',link:null,status:'active',connections:sources,details:{},checked_at:stamp,error:null,posts_today:null,posts_today_complete:false,posts_checked_at:null}];
  const detail=id=>({page:pages.find(value=>value.id===id),posts:[],posts_has_more:false,posts_checked_at:null,posts_error:null});
  const options=(id,source)=>({page_id:id,connection_id:source,current:unknownCurrent?{bio:null,bio_field:'bio',location:null,category_ids:null,category_list:[]}:{bio:id==='90000001'?'Bio A hiện tại':'Bio B hiện tại',bio_field:id==='90000001'?'bio':'about',location:{street:'12 Phố Cũ',city:'Thành phố cũ'},category_ids:['100'],category_list:[{id:'100',name:'Danh mục hiện tại'}]},supported_fields:source===2?['bio']:['bio','location','category_ids','avatar','cover'],limits:{bio_max_length:101,category_max_count:3,image_max_bytes:8388608,mime_types:['image/jpeg','image/png']}});
  await page.route('**/*',async route=>{
   const request=route.request(),url=new URL(request.url());
   if(url.origin===origin&&url.pathname==='/auth/api/me')return route.fulfill({json:{user:{id:1,username:'fixture',role:'admin',must_change_password:false},csrf_token:'fixture-via-csrf'}});
   if(url.origin===origin&&/^(?:\/pages)?\/api\//.test(url.pathname)&&!['GET','HEAD','OPTIONS'].includes(request.method()))assert.equal(request.headers()['x-via-csrf'],'fixture-via-csrf');

   if(url.origin!==origin||!url.pathname.startsWith(mount+'/')){outside.push(url.pathname);return route.abort()}
   const p=url.pathname.slice(mount.length),method=request.method();
   if(p.startsWith('/api/')){
    const binary=p.endsWith('/image'),body=request.postData()?binary?request.postDataBuffer():request.postDataJSON():undefined;
    if(method!=='GET')assert.equal(request.headers()['x-mpm-csrf'],csrf());
    if(p==='/api/session'){session++;return route.fulfill({json:{csrf_token:csrf(),app:{name:'Page Manager',version:'fixture',graph_version:'v26.0',timezone:'UTC',page_editing:editingEnabled}}})}
    if(p==='/api/overview')return route.fulfill({json:{stats:{pages:2,connections:2,working_connections:2,needs_attention:0,pages_with_posts_today:0,today_count_known:false},updated_at:stamp}});
    if(p==='/api/connections')return route.fulfill({json:{items:sources.map(value=>({...value,token_type:'user',connected:true,has_token:true,page_count:2}))}});
    if(p==='/api/pages')return route.fulfill({json:{items:pages}});
    if(p==='/api/events')return route.fulfill({json:{items:[]}});
    if(/^\/api\/pages\/\d+\/edit-options$/.test(p)){
     const id=p.split('/')[3],source=body.connection_id;optionReads.push({id,source});const value=options(id,source);
     if(delayOptions){delayOptions=false;await new Promise(resolve=>{optionsRelease=resolve})}
     return route.fulfill({json:value}).catch(()=>{});
    }
    if(/\/categories$/.test(p)){
     assert.equal(url.searchParams.get('connection_id'),'1');const q=url.searchParams.get('q');const value={items:[{id:q==='slow'?'300':'200',name:q==='slow'?'Kết quả cũ':'Danh mục mới'}],complete:true};
     if(delayCategory){delayCategory=false;await new Promise(resolve=>{categoryRelease=resolve})}
     return route.fulfill({json:value}).catch(()=>{});
    }
    if(/\/edit$|\/image$/.test(p)){
     const kind=binary?url.searchParams.get('kind'):'text',requestId=binary?url.searchParams.get('request_id'):body.request_id,source=binary?Number(url.searchParams.get('connection_id')):body.connection_id;
     assert.match(requestId,/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i);assert.equal(source,1);
     if(binary){assert.equal(request.headers()['content-type'],'image/png');assert.deepEqual(body,tinyPng)}
     mutations.push({path:p,kind,source,requestId,body:binary?null:body});
     if(mode==='network')return route.abort('failed');
     if(mode==='csrf'){mode='after-csrf';return route.fulfill({status:403,json:{detail:'Expired fixture CSRF'}})}
     const outcome=mode==='unknown-avatar'&&kind==='avatar'?'unknown':mode==='partial'?'partial':mode==='failed'?'failed':'accepted';
     return route.fulfill({json:{ok:outcome==='accepted',outcome,message:outcome==='accepted'?'Meta đã tiếp nhận yêu cầu fixture.':'Kết quả fixture cần đọc lại Page.',requires_refresh:true}});
    }
    if(/^\/api\/pages\/\d+\/refresh$/.test(p)){refreshes.push({id:p.split('/')[3],source:body.connection_id});return route.fulfill({json:detail(p.split('/')[3])})}
    if(/^\/api\/pages\/\d+$/.test(p))return route.fulfill({json:detail(p.split('/')[3])});
    return route.fulfill({status:404,json:{detail:'Unknown editor fixture API'}});
   }
   if(p==='/')return route.fulfill({path:path.join(frontend,'index.html'),contentType:'text/html'});
   if(p.startsWith('/static/')){const file=path.resolve(frontend,p.slice(8));if(!file.startsWith(frontend+path.sep)||!fs.existsSync(file))return route.abort();return route.fulfill({path:file,contentType:file.endsWith('.js')?'application/javascript':file.endsWith('.woff2')?'font/woff2':'text/css'})}
   return route.abort();
  });
  page.on('pageerror',error=>errors.push(error.message));
  await page.goto(origin+mount+'/');
  const drawer=page.getByRole('dialog',{name:'Chi tiết Page',exact:true}),editor=page.locator('#page-edit-dialog');
  async function openEditor(name='Page A · Fixture',source='1'){
   await page.getByRole('button',{name:'Xem chi tiết Page '+name,exact:true}).click();await drawer.getByRole('button',{name:'Chỉnh sửa Page',exact:true}).click();
   if(source)await editor.getByRole('combobox',{name:'Nguồn chỉnh sửa Page',exact:true}).selectOption(source);
   if(source)await editor.getByRole('checkbox',{name:'Thay đổi Bio / Giới thiệu ngắn',exact:true}).waitFor();
  }
  async function closeEditor(){await editor.getByRole('button',{name:'Đóng chỉnh sửa Page',exact:true}).click();await drawer.getByRole('button',{name:'Đóng chi tiết Page',exact:true}).click()}
  async function stage({images=true,categories=false}={}){
   await editor.getByRole('checkbox',{name:'Thay đổi Bio / Giới thiệu ngắn',exact:true}).check();await editor.getByRole('textbox',{name:'Bio mới',exact:true}).fill('Bio mới do người dùng nhập');
   await editor.getByRole('checkbox',{name:'Thay đổi Địa chỉ',exact:true}).check();await editor.getByRole('textbox',{name:'Địa chỉ · Đường / số nhà',exact:true}).fill('88 Phố Mới');
   if(categories){await editor.getByRole('checkbox',{name:'Thay đổi Danh mục',exact:true}).check();await editor.getByRole('searchbox',{name:'Tìm danh mục Page',exact:true}).fill('design');await editor.getByRole('button',{name:'Chọn danh mục Danh mục mới',exact:true}).click()}
   if(images)for(const kind of ['đại diện','bìa'])await editor.getByLabel('Chọn ảnh '+kind,{exact:true}).setInputFiles({name:'fixture-'+kind+'.png',mimeType:'image/png',buffer:tinyPng});
  }
  await openEditor();assert.equal(mutations.length,0,'Opening editor and loading options does not write Page');
  await stage({categories:true});
  assert.equal(await editor.getByRole('img',{name:'Xem trước ảnh đại diện',exact:true}).count(),1);
  assert.equal(await editor.getByRole('img',{name:'Xem trước ảnh bìa',exact:true}).count(),1);
  await editor.getByRole('button',{name:'Xem lại thay đổi',exact:true}).click();
  await editor.getByRole('heading',{name:'Xem lại thay đổi',exact:true}).waitFor();assert.equal(mutations.length,0,'Reviewing changes does not submit them');
  await editor.getByText('Nguồn: Nguồn chính · #1',{exact:true}).waitFor();
  fs.mkdirSync(path.join(root,'.runtime/ui'),{recursive:true});await page.screenshot({path:path.join(root,'.runtime/ui',mount?'mounted-page-edit-review.png':'page-edit-review.png'),fullPage:true});
  await page.setViewportSize({width:390,height:844});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,'Mobile editor never overflows the page');
  await page.screenshot({path:path.join(root,'.runtime/ui',mount?'mounted-page-edit-review-mobile.png':'page-edit-review-mobile.png'),fullPage:true});await page.setViewportSize({width:1450,height:1000});
  await editor.getByRole('button',{name:'Lưu lên Page',exact:true}).evaluate(button=>{button.click();button.click()});
  await editor.getByRole('heading',{name:'Kết quả gửi thay đổi',exact:true}).waitFor();
  assert.deepEqual(mutations.map(value=>value.kind),['text','avatar','cover']);assert.equal(new Set(mutations.map(value=>value.requestId)).size,3,'Each frozen operation has a distinct stable journal UUID');
  assert.equal(mutations[0].path,'/api/pages/90000001/edit');assert.equal(mutations[0].body.bio,'Bio mới do người dùng nhập');assert.equal(mutations[0].body.location.street,'88 Phố Mới');assert.deepEqual(mutations[0].body.category_ids,['100','200']);
  assert.equal(await editor.getByRole('button',{name:'Lưu lên Page',exact:true}).count(),0,'Attempted review cannot be submitted again');
  await closeEditor();
  const beforeSource=mutations.length;
  await openEditor();await stage();await editor.getByRole('combobox',{name:'Nguồn chỉnh sửa Page',exact:true}).selectOption('2');await editor.getByRole('checkbox',{name:'Thay đổi Bio / Giới thiệu ngắn',exact:true}).waitFor();
  assert.equal(await editor.getByRole('textbox',{name:'Bio mới',exact:true}).inputValue(),'Bio A hiện tại','Changing source discards staged edits');
  assert.equal(await editor.getByRole('img').count(),0,'Changing source clears local image previews');
  assert.equal(await editor.getByRole('checkbox',{name:'Thay đổi Địa chỉ',exact:true}).isDisabled(),true,'Unsupported fields stay disabled');assert.equal(mutations.length,beforeSource);await closeEditor();
  for(const outcome of ['unknown-avatar','partial','failed','network','csrf']){
   mode=outcome;const start=mutations.length;await openEditor();await stage();await editor.getByRole('button',{name:'Xem lại thay đổi',exact:true}).click();await editor.getByRole('button',{name:'Lưu lên Page',exact:true}).click();await editor.getByRole('heading',{name:'Kết quả gửi thay đổi',exact:true}).waitFor();
   assert.deepEqual(mutations.slice(start).map(value=>value.kind),outcome==='unknown-avatar'?['text','avatar']:['text'],'No later operation or automatic retry after '+outcome);
   await page.waitForTimeout(80);assert.equal(mutations.length-start,outcome==='unknown-avatar'?2:1);await closeEditor();
  }
  mode='accepted';delayOptions=true;await openEditor('Page A · Fixture','');await editor.getByRole('combobox',{name:'Nguồn chỉnh sửa Page',exact:true}).selectOption('1');
  for(let i=0;i<50&&!optionsRelease;i++)await page.waitForTimeout(20);assert.ok(optionsRelease);
  await closeEditor();await openEditor('Page B · Fixture');optionsRelease();optionsRelease=null;await page.waitForTimeout(80);
  assert.equal(await editor.getByRole('textbox',{name:'Bio mới',exact:true}).inputValue(),'Bio B hiện tại','Stale Page A options cannot overwrite Page B');
  await editor.getByText('Đang sửa phần Giới thiệu ngắn của Page.',{exact:true}).waitFor();
  assert.equal(await editor.getByRole('img').count(),0);await closeEditor();
  await openEditor();await editor.getByRole('checkbox',{name:'Thay đổi Danh mục',exact:true}).check();delayCategory=true;await editor.getByRole('searchbox',{name:'Tìm danh mục Page',exact:true}).fill('slow');
  for(let i=0;i<50&&!categoryRelease;i++)await page.waitForTimeout(20);assert.ok(categoryRelease);
  await editor.getByRole('searchbox',{name:'Tìm danh mục Page',exact:true}).fill('fast');await editor.getByRole('button',{name:'Chọn danh mục Danh mục mới',exact:true}).waitFor();categoryRelease();categoryRelease=null;await page.waitForTimeout(80);
  assert.equal(await editor.getByRole('button',{name:'Chọn danh mục Kết quả cũ',exact:true}).count(),0,'Stale category search results cannot replace newer query');
  await closeEditor();unknownCurrent=true;await openEditor();const beforeUnknown=mutations.length;
  for(const field of ['Bio / Giới thiệu ngắn','Địa chỉ','Danh mục'])await editor.getByRole('checkbox',{name:'Thay đổi '+field,exact:true}).check();
  await editor.getByRole('button',{name:'Xem lại thay đổi',exact:true}).click();
  await editor.getByText('Chưa có thay đổi để gửi. Chọn mục cần sửa hoặc chọn ảnh mới.',{exact:true}).waitFor();assert.equal(mutations.length,beforeUnknown,'Unknown unchanged fields are not submitted as empty values');
  await stage({images:false,categories:true});await editor.getByRole('button',{name:'Xem lại thay đổi',exact:true}).click();
  assert.equal(await editor.getByText('Chưa đọc được dữ liệu hiện tại',{exact:true}).count(),3,'Unknown current bio/address/categories are never presented as blank');
  assert.equal(mutations.length,beforeUnknown);await editor.getByRole('button',{name:'Lưu lên Page',exact:true}).click();await editor.getByRole('heading',{name:'Kết quả gửi thay đổi',exact:true}).waitFor();
  const beforeReadback=mutations.length;await editor.getByRole('button',{name:'Đọc lại Page',exact:true}).click();await editor.waitFor({state:'hidden'});
  await page.waitForFunction(()=>!document.querySelector('#page-dialog button.button.primary')?.disabled);
  assert.deepEqual(refreshes,[{id:'90000001',source:1}],'Readback uses the frozen exact Page/source');assert.equal(mutations.length,beforeReadback,'Reading back never replays a mutation');
  editingEnabled=false;const beforeOldServer=mutations.length,optionsBeforeOldServer=optionReads.length;await page.reload();
  await page.getByRole('button',{name:'Xem chi tiết Page Page A · Fixture',exact:true}).click();
  const oldServerEdit=drawer.getByRole('button',{name:'Chỉnh sửa Page',exact:true});assert.equal(await oldServerEdit.isDisabled(),true,'Old server capability keeps edit action disabled');
  assert.equal(await oldServerEdit.getAttribute('title'),'Mở lại server để dùng chỉnh sửa Page');assert.equal(mutations.length,beforeOldServer);assert.equal(optionReads.length,optionsBeforeOldServer);
  assert.equal(await page.evaluate(()=>localStorage.length),0);assert.equal(await page.evaluate(()=>sessionStorage.length),0);assert.deepEqual(errors,[]);assert.deepEqual(outside,[]);
  console.log('PASS: '+(mount?'mounted':'standalone')+' offline Page editing: exact Page/source, staged images/fields, category IDs selected from search, review before Save, operation UUIDs/order, double-submit guard, stop/no retry on partial/failed/unknown/network/CSRF, source reset and stale options/category guards. Synthetic fixtures only.');
 }finally{await browser.close()}
})().catch(error=>{console.error(error);process.exit(1)});
