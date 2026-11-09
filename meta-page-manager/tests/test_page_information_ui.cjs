// Offline Page information statuses. Synthetic cache only: no Meta or app writes.
const {chromium}=require('../../chrome-manager/frontend/node_modules/playwright');
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const frontend=path.resolve(__dirname,'../frontend'),origin='http://127.0.0.1:8000',base='/pages';
const stamp='2030-11-02T10:00:00Z',pad=value=>String(value).padStart(3,'0');
(async()=>{
 const browser=await chromium.launch({channel:'chrome',headless:true});
 try{
  const context=await browser.newContext({offline:true,serviceWorkers:'block',viewport:{width:1540,height:1050}});
  const page=await context.newPage(),reads=[],writes=[],outside=[],errors=[];
  page.setDefaultTimeout(8000);
  const connection={id:1,label:'Synthetic information source',status:'working',connected:true,has_token:true};
  const pages=Array.from({length:26},(_,index)=>({
   id:String(90000001+index),name:'Info Page '+pad(index+1),category:'Fixture',
   // Deliberately independent: even Pages are disconnected but have accepted edits.
   status:(index+1)%2===0?'disconnected':'active',
   ...((index+1)===3?{}:{information_status:(index+1)%2===0?'updated':'not_updated'}),
   connections:[{...connection,has_page_token:true,sources:['assigned_pages']}],details:{},
   checked_at:stamp,error:null,posts_today:null,posts_today_complete:false,posts_checked_at:null,
  }));
  await page.route('**/*',async route=>{
   const request=route.request(),url=new URL(request.url());
   if(url.origin===origin&&url.pathname==='/auth/api/me')return route.fulfill({json:{user:{id:1,username:'fixture',role:'admin',must_change_password:false},csrf_token:'fixture-via-csrf'}});
   if(url.origin===origin&&/^(?:\/pages)?\/api\//.test(url.pathname)&&!['GET','HEAD','OPTIONS'].includes(request.method()))assert.equal(request.headers()['x-via-csrf'],'fixture-via-csrf');

   if(url.origin!==origin||!url.pathname.startsWith(base+'/')){outside.push(request.url());return route.abort()}
   const p=url.pathname.slice(base.length);
   if(p.startsWith('/api/')){
    if(request.method()!=='GET'){writes.push({path:p,method:request.method()});return route.fulfill({status:405,json:{detail:'Read-only fixture'}})}
    reads.push({path:p,query:Object.fromEntries(url.searchParams)});
    if(p==='/api/session')return route.fulfill({json:{csrf_token:'offline-information-csrf',app:{name:'Page Manager',graph_version:'fixture',timezone:'America/Los_Angeles',page_editing:false}}});
    if(p==='/api/overview')return route.fulfill({json:{stats:{pages:pages.length,connections:1},updated_at:stamp}});
    if(p==='/api/connections/options')return route.fulfill({json:{items:[connection],total:1,limit:100,offset:0}});
    if(p==='/api/pages'){
     let rows=pages;
     const information=url.searchParams.get('information_status')||'',search=url.searchParams.get('search')||'';
     if(information)rows=rows.filter(row=>(row.information_status||'not_updated')===information);
     if(search)rows=rows.filter(row=>(row.name+' '+row.id).toLowerCase().includes(search.toLowerCase()));
     const limit=Number(url.searchParams.get('limit')||10),offset=Number(url.searchParams.get('offset')||0);
     assert.ok(limit<=10,'The status filter reads a server window, not the entire catalog');
     return route.fulfill({json:{items:rows.slice(offset,offset+limit),total:rows.length,limit,offset}});
    }
    return route.fulfill({status:404,json:{detail:'Unknown offline information endpoint'}});
   }
   if(p==='/')return route.fulfill({path:path.join(frontend,'index.html'),contentType:'text/html'});
   if(p.startsWith('/static/')){
    const file=path.resolve(frontend,p.slice(8));
    if(!file.startsWith(frontend+path.sep)||!fs.existsSync(file))return route.abort();
    return route.fulfill({path:file,contentType:file.endsWith('.js')?'application/javascript':file.endsWith('.woff2')?'font/woff2':'text/css'});
   }
   return route.abort();
  });
  page.on('pageerror',error=>errors.push(error.message));
  const row=name=>page.getByRole('button',{name:'Xem chi tiết Page '+name,exact:true}).locator('xpath=ancestor::tr');
  const tableRows=()=>page.locator('.pages-table tbody>tr');
  const status=name=>row(name).locator('td').nth(2);
  const filter=()=>page.getByRole('combobox',{name:'Lọc thông tin Page',exact:true});
  const pager=()=>page.getByRole('navigation',{name:'Phân trang Pages',exact:true});
  const control=name=>pager().getByRole('button',{name:name+' · Pages',exact:true});
  const pageReads=()=>reads.filter(value=>value.path==='/api/pages');
  await page.goto(origin+base+'/');await row('Info Page 001').waitFor();
  assert.equal(await tableRows().count(),10);
  assert.equal((await status('Info Page 001').innerText()).trim(),'Chưa đổi thông tin','Active access is not proof of a successful edit');
  assert.equal((await status('Info Page 002').innerText()).trim(),'Đã đổi thông tin','A disconnected source must not erase durable edit history');
  assert.equal((await status('Info Page 003').innerText()).trim(),'Chưa đổi thông tin','A missing legacy field must not be counted as updated');
  assert.equal(await page.getByRole('combobox',{name:'Lọc tình trạng Page',exact:true}).count(),0);
  assert.deepEqual(await filter().locator('option').evaluateAll(options=>options.map(option=>option.value)),['','updated','not_updated']);
  await control('Trang cuối').click();await row('Info Page 026').waitFor();
  assert.equal(await tableRows().count(),6);
  await filter().selectOption('updated');await row('Info Page 002').waitFor();
  assert.equal(await tableRows().count(),10);assert.match(await pager().innerText(),/1–10 \/ 13 Pages/);
  assert.equal(await control('Trang trước').isDisabled(),true,'Changing status resets the SQL page offset');
  assert.equal(pageReads().at(-1).query.information_status,'updated');assert.equal(pageReads().at(-1).query.offset,'0');
  assert.ok((await tableRows().locator('td:nth-child(3)').allTextContents()).every(value=>value.trim()==='Đã đổi thông tin'));
  await control('Trang cuối').click();await row('Info Page 026').waitFor();
  assert.equal(await tableRows().count(),3);assert.equal(pageReads().at(-1).query.offset,'10');
  assert.equal(pageReads().at(-1).query.information_status,'updated');
  await filter().selectOption('not_updated');await row('Info Page 001').waitFor();
  assert.equal(await tableRows().count(),10);assert.match(await pager().innerText(),/1–10 \/ 13 Pages/);
  assert.equal(pageReads().at(-1).query.information_status,'not_updated');assert.equal(pageReads().at(-1).query.offset,'0');
  assert.ok((await tableRows().locator('td:nth-child(3)').allTextContents()).every(value=>value.trim()==='Chưa đổi thông tin'));
  await control('Trang cuối').click();await row('Info Page 025').waitFor();
  assert.equal(await tableRows().count(),3);
  await filter().selectOption('');await row('Info Page 001').waitFor();
  assert.equal(await tableRows().count(),10);assert.match(await pager().innerText(),/1–10 \/ 26 Pages/);
  assert.ok(pageReads().every(value=>!value.query.status),'Information filtering never reuses token health as edit status');
  assert.deepEqual(writes,[]);assert.deepEqual(outside,[]);assert.deepEqual(errors,[]);
  assert.equal(await page.evaluate(()=>localStorage.length+sessionStorage.length),0);
  console.log('PASS: mounted offline Page information statuses, independent token health, missing legacy data, server-filtered pagination/reset, zero writes/network/storage.');
 }finally{await browser.close()}
})().catch(error=>{console.error(error);process.exitCode=1});
