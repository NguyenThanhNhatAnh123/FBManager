// Disposable offline browser. Only synthetic API responses; never opens Meta or another local app.
const {chromium}=require('../../chrome-manager/frontend/node_modules/playwright');
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const root=path.resolve(__dirname,'..'),frontend=path.join(root,'frontend'),output=path.join(root,'.runtime','ui');
const mountBase=process.argv.includes('--mounted')?'/pages':'',origin=mountBase?'http://127.0.0.1:8000':'http://127.0.0.1:8010';
const screenshot=name=>path.join(output,(mountBase?'mounted-':'')+name);
(async()=>{
 fs.mkdirSync(output,{recursive:true});const browser=await chromium.launch({channel:'chrome',headless:true});
 try{
  const context=await browser.newContext({viewport:{width:1560,height:1050},offline:true});
  const chromePreferences=mountBase?{cm_sidebar_compact:'true',cm_facebook_timezone:'America/Los_Angeles'}:{};
  if(mountBase)await context.addInitScript(values=>{for(const [key,value] of Object.entries(values))if(localStorage.getItem(key)===null)localStorage.setItem(key,value)},chromePreferences);
  const page=await context.newPage(),errors=[],outside=[],writes=[],publicPaths=[];let sessionCalls=0,rejectTokenOnce=false,echoTokenOnce=false,detailRelease=null,delayFirstPage=false,catalogRelease=null,delayCatalogOnce=false,appTimezone='Asia/Ho_Chi_Minh';
  const now=()=>new Date().toISOString(),csrf=()=>`fixture-csrf-${sessionCalls}`;
  const connections=[
   {id:1,label:'BM Thương hiệu · Chính',token_type:'user',app_id:'123456',business_id:'789012',identity_id:'10000001',identity_name:'Nguyễn Minh Anh',status:'working',has_token:true,permissions:[{permission:'pages_show_list',status:'granted'},{permission:'pages_read_engagement',status:'granted'},{permission:'read_insights',status:'declined'}],expires_at:null,checked_at:now(),synced_at:now(),error:null,connected:true,page_count:2},
   {id:2,label:'Studio · System user',token_type:'system_user',app_id:null,business_id:'789012',identity_id:'10000002',identity_name:'Studio Operations',status:'permission_limited',has_token:true,permissions:null,expires_at:'2030-11-30T12:00:00Z',checked_at:now(),synced_at:now(),error:'Một phạm vi chưa được cấp quyền; các Page đã lưu được giữ lại.',connected:true,page_count:2},
   {id:3,label:'Page Token · Dự phòng',token_type:'page',app_id:null,business_id:null,identity_id:'90000003',identity_name:'Atelier North',status:'unchecked',has_token:true,permissions:null,expires_at:null,checked_at:null,synced_at:null,error:null,connected:true,page_count:1},
  ];
  const edge=id=>{const c=connections.find(value=>value.id===id);return {id,label:c.label,status:c.status,sources:['me/accounts'],tasks:['CREATE_CONTENT','ANALYZE'],has_page_token:true}};
  const pages=[
   {id:'90000001',name:'The Sunday Studio',category:'Thương hiệu & thiết kế',link:'https://www.facebook.com/profile.php?id=90000001&ref=bookmarks',status:'active',connections:[edge(1),edge(2)],details:{about:'Những ý tưởng nhỏ cho một ngày đáng nhớ.',website:'https://example.test',followers_count:12840,location:{street:'12 Nguyễn Huệ',city:'TP. Hồ Chí Minh'}},checked_at:now(),error:null,posts_today:3,posts_today_complete:true,posts_checked_at:now()},
   {id:'90000002',name:'Café Mùa Nắng',category:'Quán cà phê',link:'https://www.facebook.com/cafe-fixture',status:'needs_attention',connections:[edge(1),edge(2)],details:{followers_count:5340,phone:'0123 000 111'},checked_at:now(),error:null,posts_today:1,posts_today_complete:false,posts_checked_at:now()},
   {id:'90000003',name:'Atelier North',category:'Trang trí nhà cửa',link:null,status:'active',connections:[edge(3)],details:{about:'Vật dụng có câu chuyện riêng.',followers_count:2150},checked_at:now(),error:null,posts_today:null,posts_today_complete:false,posts_checked_at:null},
   {id:'90000004',name:'Maison Bloom',category:'Cửa hàng hoa',link:null,status:'active',connections:[edge(1)],details:{},checked_at:now(),error:null,posts_today:0,posts_today_complete:true,posts_checked_at:now()},
  ];
  const posts=id=>[{id:id+'_post1',message:id==='90000003'?'Bài viết riêng của Atelier North.':'Một góc sáng mới cho cuối tuần. Khám phá bộ sưu tập vừa đến cùng Sunday Studio. #MadeForSunday',created_time:now(),permalink_url:'https://www.facebook.com/'+id+'/posts/123?access_token=must-not-escape&ref=fixture'},
                   {id:id+'_post2',message:'<img src=x onerror="window.fixtureXss=true"> Nội dung được hiển thị như văn bản.',created_time:now(),permalink_url:'https://evil.test/not-facebook'}];
  const detail=(id,timezone)=>({page:{...pages.find(value=>value.id===id),...(id==='90000001'&&timezone==='UTC'?{posts_today:9}:{})},posts:posts(id),posts_has_more:id==='90000002',posts_checked_at:now(),posts_error:id==='90000002'?'Chỉ đọc được một phần dữ liệu bài viết.':null});
  const events=[{id:1,action:'sync',status:'success',message:'Đồng bộ hoàn tất. Danh mục Page được cập nhật.',connection_label:connections[0].label,created_at:now()},
                {id:2,action:'sync',status:'partial',message:'Đọc một phần Page. Giữ các liên kết cũ chưa xác nhận lại.',connection_label:connections[1].label,created_at:now()},
                {id:3,action:'check',status:'success',message:'Đã xác nhận danh tính và tình trạng kết nối.',connection_label:connections[0].label,created_at:now()}];
  await page.route('**/*',async route=>{
   const request=route.request(),url=new URL(request.url());
   if(url.origin!==origin){outside.push(url.origin);return route.abort()}
   if(url.pathname==='/auth/api/me')return route.fulfill({json:{user:{id:1,username:'fixture',role:'admin',must_change_password:false},csrf_token:'fixture-via-csrf'}});
   publicPaths.push(url.pathname);
   if(mountBase)assert.ok(url.pathname.startsWith(mountBase+'/'),'Mounted UI must never request Chrome Manager root APIs/assets: '+url.pathname);
   const pathname=url.pathname.slice(mountBase.length)||'/',method=request.method();let body=request.postData()?request.postDataJSON():undefined;
   if(pathname.startsWith('/api/')){
    if(method!=='GET'){writes.push({path:pathname,publicPath:url.pathname,method,body,timezone:url.searchParams.get('timezone')});assert.equal(request.headers()['x-mpm-csrf'],csrf());assert.equal(request.headers()['x-via-csrf'],'fixture-via-csrf')}
    let data;
    if(pathname==='/api/session'){sessionCalls++;data={csrf_token:csrf(),app:{name:'Page Manager',version:'1.0.0',graph_version:'v26.0',timezone:appTimezone,page_editing:true,chrome_manager_url:mountBase?origin+'/':'http://127.0.0.1:8000/',page_manager_url:origin+mountBase+'/'}}}
    else if(pathname==='/api/overview')data={stats:{pages:pages.length,connections:connections.length,working_connections:1,needs_attention:1,pages_with_posts_today:2,today_count_known:3},updated_at:now()};
    else if(pathname==='/api/connections'&&method==='GET')data={items:connections};
    else if(pathname==='/api/connections'&&method==='POST'){
     if(echoTokenOnce){echoTokenOnce=false;return route.fulfill({status:422,json:{detail:'Unsafe echo: '+body.token+' · fixture response'}})}
     if(rejectTokenOnce){rejectTokenOnce=false;return route.fulfill({status:403,json:{detail:'CSRF session expired',code:'csrf_invalid'}})}
     const connection={id:connections.length+1,...body,status:'unchecked',has_token:true,permissions:null,expires_at:null,identity_id:null,identity_name:null,checked_at:null,synced_at:null,error:null,connected:true,page_count:0};delete connection.token;connections.push(connection);data=connection;
    }else if(/^\/api\/connections\/\d+\/check$/.test(pathname)){const c=connections.find(value=>value.id===Number(pathname.split('/')[3]));data={connection:c}}
    else if(/^\/api\/connections\/\d+\/sync$/.test(pathname)){const c=connections.find(value=>value.id===Number(pathname.split('/')[3]));data={connection:c,pages_seen:2,complete:false}}
    else if(/^\/api\/connections\/\d+$/.test(pathname)&&method==='DELETE'){const c=connections.find(value=>value.id===Number(pathname.split('/')[3]));Object.assign(c,{connected:false,has_token:false,status:'disconnected'});data={ok:true}}
    else if(pathname==='/api/pages'){
     let values=pages;
     if(url.searchParams.get('connection_id'))values=values.filter(p=>p.connections.some(c=>c.id===Number(url.searchParams.get('connection_id'))));
     if(url.searchParams.get('search'))values=values.filter(p=>(p.name+' '+p.id).toLowerCase().includes(url.searchParams.get('search').toLowerCase()));
     if(url.searchParams.get('status'))values=values.filter(p=>p.status===url.searchParams.get('status'));
     data={items:values};
     if(delayCatalogOnce){delayCatalogOnce=false;data=JSON.parse(JSON.stringify(data));await new Promise(resolve=>{catalogRelease=resolve})}
    }else if(/^\/api\/pages\/\d+\/refresh$/.test(pathname)){
     const id=pathname.split('/')[3];assert.ok(pages.find(p=>p.id===id).connections.some(c=>c.id===body.connection_id)||body.connection_id===undefined);assert.equal(body.include_posts,true);assert.equal(body.limit,25);data=detail(id,url.searchParams.get('timezone'));
    }else if(/^\/api\/pages\/\d+$/.test(pathname)){
     const id=pathname.split('/')[3];
     if(delayFirstPage&&id==='90000001'){await new Promise(resolve=>{detailRelease=resolve})}
     data=detail(id,url.searchParams.get('timezone'));
    }else if(pathname==='/api/events')data={items:events};
    else return route.fulfill({status:404,json:{detail:'Unknown fixture API'}});
    return route.fulfill({json:data}).catch(()=>{});
   }
   if(['/','/index.html','/schedule','/saved','/settings'].includes(pathname))return route.fulfill({path:path.join(frontend,'index.html'),contentType:'text/html'});
   if(pathname.startsWith('/static/')){const file=path.resolve(frontend,pathname.slice(8));if(!file.startsWith(frontend+path.sep)||!fs.existsSync(file))return route.abort();return route.fulfill({path:file,contentType:file.endsWith('.js')?'application/javascript':file.endsWith('.woff2')?'font/woff2':file.endsWith('.woff')?'font/woff':'text/css'})}
   return route.abort();
  });
  page.on('pageerror',error=>errors.push(error.message));
  await page.goto(origin+mountBase+'/');
  await page.getByRole('heading',{name:'Pages',exact:true}).waitFor();
  await page.getByRole('button',{name:'Xem chi tiết Page The Sunday Studio',exact:true}).waitFor();
  assert.equal(writes.length,0,'Opening app/cache views never calls Meta synchronization');
  assert.equal(await page.getByRole('link',{name:'Mở Chrome Manager',exact:true}).getAttribute('href'),mountBase?'/':'http://127.0.0.1:8000/');
  assert.equal(await page.getByRole('link',{name:'Mở Chrome Manager',exact:true}).evaluate(link=>link.href),'http://127.0.0.1:8000/');
  const navigation=page.getByRole('navigation',{name:'Điều hướng chính',exact:true});
  assert.deepEqual(await navigation.getByRole('button').evaluateAll(buttons=>buttons.map(button=>button.getAttribute('aria-label'))),['Pages','Lịch đăng Page','Lịch đã lưu'],'Saved schedules is directly below schedule creation');
  assert.equal(await page.getByRole('button',{name:'Dashboard',exact:true}).count(),0,'Page Manager has no Dashboard route');
  assert.equal(await page.getByRole('button',{name:'Kiểm tra',exact:true}).count(),0,'Admin token controls remain in Settings');
  assert.equal(await page.getByRole('button',{name:'Thêm kết nối',exact:true}).count(),0,'Pages keeps token setup in Settings');
  await page.screenshot({path:screenshot('pages.png'),fullPage:true});
  await page.getByRole('button',{name:'Settings',exact:true}).click();
  await page.getByRole('tab',{name:'Kết nối token',exact:true}).click();
  await page.getByRole('heading',{name:'Settings',exact:true}).waitFor();
  assert.equal(await page.getByRole('tab',{name:'Kết nối token',exact:true}).getAttribute('aria-selected'),'true','Token operations stay reachable in Settings');
  await page.getByRole('tab',{name:'Tổng quan',exact:true}).click();
  await page.getByRole('heading',{name:'Dữ liệu & không gian',exact:true}).waitFor();
  await page.screenshot({path:screenshot('settings.png'),fullPage:true});
  await page.getByRole('tab',{name:'Tổng quan',exact:true}).press('ArrowRight');
  assert.equal(await page.getByRole('tab',{name:'Kết nối token',exact:true}).getAttribute('aria-selected'),'true','Settings tabs support keyboard navigation');
  await page.getByRole('tab',{name:'Kết nối token',exact:true}).press('ArrowDown');
  assert.equal(await page.getByRole('tab',{name:'Hoạt động',exact:true}).getAttribute('aria-selected'),'true','Vertical Settings supports arrow down');
  await page.getByRole('tab',{name:'Hoạt động',exact:true}).press('ArrowUp');
  assert.equal(await page.getByRole('tab',{name:'Kết nối token',exact:true}).getAttribute('aria-selected'),'true','Vertical Settings supports arrow up');
  await page.getByRole('button',{name:'Lịch đăng Page',exact:true}).click();
  await page.getByRole('heading',{name:'Chưa có tính năng lên lịch bằng token',exact:true}).waitFor();
  assert.equal(await page.getByRole('button',{name:/Tạo lịch|Đăng ngay/}).count(),0,'Read-only scheduler does not present fake publishing actions');
  await page.getByRole('button',{name:'Pages',exact:true}).click();
  await page.getByRole('button',{name:'Xem chi tiết Page The Sunday Studio',exact:true}).waitFor();
  assert.equal(await page.getByRole('button',{name:'Xem chi tiết Page The Sunday Studio',exact:true}).count(),1,'Multi-token Page appears once');
  const sunday=page.locator('tbody tr').filter({has:page.getByRole('button',{name:'Xem chi tiết Page The Sunday Studio',exact:true})});
  await sunday.getByText('BM Thương hiệu · Chính',{exact:true}).waitFor();await sunday.getByText('Studio · System user',{exact:true}).waitFor();
  await page.getByRole('combobox',{name:'Lọc bài hôm nay',exact:true}).selectOption('unknown');
  assert.equal(await page.getByRole('button',{name:'Xem chi tiết Page Maison Bloom',exact:true}).count(),0,'Known 0 and unknown are distinct');
  await page.getByRole('button',{name:'Xem chi tiết Page Atelier North',exact:true}).waitFor();
  await page.getByRole('combobox',{name:'Lọc bài hôm nay',exact:true}).selectOption('');
  await page.getByRole('searchbox',{name:'Tìm Page',exact:true}).fill('Sunday');
  await page.getByRole('button',{name:'Xem chi tiết Page Café Mùa Nắng',exact:true}).waitFor({state:'hidden'});
  assert.equal(await page.getByRole('searchbox',{name:'Tìm Page',exact:true}).inputValue(),'Sunday');
  await page.getByRole('searchbox',{name:'Tìm Page',exact:true}).fill('');await page.getByRole('button',{name:'Xem chi tiết Page Café Mùa Nắng',exact:true}).waitFor();
  // A full refresh and a filtered catalog share the same generation. The old
  // response may still update overview/activity, but cannot repopulate old Pages.
  await page.getByRole('combobox',{name:'Lọc nguồn token',exact:true}).selectOption('1');
  await page.getByRole('button',{name:'Xem chi tiết Page Maison Bloom',exact:true}).waitFor();
  delayCatalogOnce=true;await page.getByRole('button',{name:'Làm mới',exact:true}).click();
  for(let i=0;i<50&&!catalogRelease;i++)await page.waitForTimeout(20);assert.ok(catalogRelease);
  await page.getByRole('combobox',{name:'Lọc nguồn token',exact:true}).selectOption('3');
  await page.getByRole('button',{name:'Xem chi tiết Page Atelier North',exact:true}).waitFor();
  catalogRelease();catalogRelease=null;
  await page.getByRole('button',{name:'Làm mới',exact:true}).waitFor({state:'visible'});
  await page.waitForFunction(()=>!Array.from(document.querySelectorAll('button')).find(b=>b.textContent==='Làm mới')?.disabled);
  // A pending search debounce may launch a newer filtered GET after the full
  // refresh finishes. Assert its settled rows rather than the loading skeleton.
  await page.getByRole('button',{name:'Xem chi tiết Page Atelier North',exact:true}).waitFor();
  assert.equal(await page.getByRole('combobox',{name:'Lọc nguồn token',exact:true}).inputValue(),'3');
  assert.equal(await page.getByRole('button',{name:'Xem chi tiết Page The Sunday Studio',exact:true}).count(),0,'Slow full reload cannot overwrite newer connection filter');
  assert.equal(await page.getByRole('button',{name:'Xem chi tiết Page Atelier North',exact:true}).count(),1);
  await page.getByRole('combobox',{name:'Lọc nguồn token',exact:true}).selectOption('');
  await page.getByRole('button',{name:'Xem chi tiết Page The Sunday Studio',exact:true}).waitFor();
  await page.getByRole('button',{name:'Xem chi tiết Page The Sunday Studio',exact:true}).click();
  const drawer=page.getByRole('dialog',{name:'Chi tiết Page',exact:true});
  await drawer.getByRole('heading',{name:'The Sunday Studio',exact:true}).waitFor();
  await drawer.getByRole('combobox',{name:'Nguồn đọc Page',exact:true}).selectOption('2');
  await drawer.getByRole('button',{name:'Cập nhật thông tin & bài viết',exact:true}).click();
  await page.waitForFunction(()=>!document.querySelector('#page-dialog button.button.primary')?.disabled);
  assert.deepEqual(writes.find(value=>value.path==='/api/pages/90000001/refresh').body,{connection_id:2,include_posts:true,limit:25});
  assert.equal(writes.find(value=>value.path==='/api/pages/90000001/refresh').timezone,'Asia/Ho_Chi_Minh','Refresh preserves workspace timezone');
  assert.equal(await drawer.getByRole('link',{name:'Xem bài trên Facebook',exact:true}).count(),1,'Unsafe external post link is never rendered');
  assert.equal((await drawer.getByRole('link',{name:'Xem bài trên Facebook',exact:true}).getAttribute('href')).includes('access_token'),false);
  assert.equal(await page.evaluate(()=>window.fixtureXss||false),false,'Post content is text, never HTML');
  await page.screenshot({path:screenshot('page-detail.png'),fullPage:true});
  await drawer.getByRole('button',{name:'Đóng chi tiết Page',exact:true}).click();
  // Make abort a no-op in this disposable fixture so the request-generation
  // guard itself must protect B against a late A response.
  await page.evaluate(()=>{AbortController.prototype.abort=function(){}});delayFirstPage=true;
  await page.getByRole('button',{name:'Xem chi tiết Page The Sunday Studio',exact:true}).click();
  await page.waitForFunction(()=>document.querySelector('#page-dialog [role="status"]'));
  for(let i=0;i<50&&!detailRelease;i++)await page.waitForTimeout(20);assert.ok(detailRelease);
  await drawer.getByRole('button',{name:'Đóng chi tiết Page',exact:true}).click();
  await page.getByRole('button',{name:'Xem chi tiết Page Atelier North',exact:true}).click();
  await drawer.getByRole('heading',{name:'Atelier North',exact:true}).waitFor();detailRelease();detailRelease=null;delayFirstPage=false;
  await page.waitForTimeout(150);
  assert.equal(await drawer.getByRole('heading',{name:'Atelier North',exact:true}).count(),1,'Delayed Page A cannot overwrite selected Page B');
  assert.equal(await drawer.getByRole('heading',{name:'The Sunday Studio',exact:true}).count(),0);
  assert.equal(await drawer.getByRole('combobox',{name:'Nguồn đọc Page',exact:true}).inputValue(),'','A source selection is never carried to another Page');
  await drawer.getByRole('button',{name:'Đóng chi tiết Page',exact:true}).click();
  await page.getByRole('button',{name:'Settings',exact:true}).click();
  await page.getByRole('tab',{name:'Kết nối token',exact:true}).click();
  await page.getByText('Business ID đã khai báo: 789012',{exact:true}).first().waitFor();
  await page.getByText('read_insights · chưa cấp',{exact:true}).waitFor();
  await page.screenshot({path:screenshot('connections.png'),fullPage:true});
  await page.getByRole('button',{name:'Thêm kết nối',exact:true}).click();
  const modal=page.getByRole('dialog',{name:'Thêm kết nối token',exact:true}),token=modal.locator('input[name="token"]');
  assert.equal(await token.getAttribute('type'),'password');
  await token.fill('EAA_SYNTHETIC_SECRET_NEVER_STORE');await modal.getByRole('button',{name:'Hủy',exact:true}).click();
  await page.getByRole('button',{name:'Thêm kết nối',exact:true}).click();assert.equal(await token.inputValue(),'','Token is cleared on modal close');
  await modal.getByLabel('Tên kết nối / ghi chú BM',{exact:true}).fill('Kết nối fixture mới');
  await token.fill('123|SYNTHETIC_APP_SECRET');await modal.getByRole('button',{name:'Lưu kết nối',exact:true}).click();
  await modal.getByText('Token ứng dụng APP_ID|APP_SECRET không được hỗ trợ. Dùng token người dùng, system user hoặc Page.',{exact:true}).waitFor();
  const longToken='EAA_LONG_'+('x'.repeat(900));echoTokenOnce=true;await token.fill(longToken);
  await modal.getByRole('button',{name:'Lưu kết nối',exact:true}).click();
  await modal.getByText('Unsafe echo: [bí mật đã ẩn] · fixture response',{exact:true}).waitFor();
  assert.equal((await modal.innerText()).includes(longToken.slice(0,60)),false,'Secret redaction must happen before error truncation');
  const before=writes.filter(value=>value.path==='/api/connections'&&value.method==='POST').length,sessionsBefore=sessionCalls;
  await token.fill('EAA_SYNTHETIC_SECRET_NEVER_STORE');rejectTokenOnce=true;
  await modal.getByRole('button',{name:'Lưu kết nối',exact:true}).click();
  await modal.getByText('Phiên làm việc đã được làm mới. Kiểm tra nội dung và bấm lại thao tác nếu cần.',{exact:true}).waitFor();
  await page.waitForTimeout(120);
  assert.equal(writes.filter(value=>value.path==='/api/connections'&&value.method==='POST').length,before+1,'A failed secret POST is never replayed automatically');
  assert.equal(sessionCalls,sessionsBefore+1,'Session initializes once after CSRF failure');
  await modal.getByRole('button',{name:'Lưu kết nối',exact:true}).click();await modal.waitFor({state:'hidden'});
  assert.equal(await page.locator('input[name="token"]').count(),0,'Token input is removed after save');
  await page.getByRole('article',{name:'Kết nối Kết nối fixture mới',exact:true}).waitFor();
  assert.deepEqual(await page.evaluate(()=>Object.fromEntries(Object.entries(localStorage))),chromePreferences,'Page Manager leaves existing same-origin Chrome preferences unchanged and stores no token');
  assert.equal(await page.evaluate(()=>sessionStorage.length),0);
  assert.equal((await page.content()).includes('EAA_SYNTHETIC_SECRET_NEVER_STORE'),false,'No token readback in DOM');
  assert.equal(page.url().includes('EAA_'),false);
  const first=page.getByRole('article',{name:'Kết nối BM Thương hiệu · Chính',exact:true});
  await first.getByRole('button',{name:'Đồng bộ Page',exact:true}).click();
  await page.getByText('Đồng bộ một phần: đã đọc 2 Page. Các liên kết cũ được giữ.',{exact:true}).waitFor();
  await page.getByRole('tab',{name:'Hoạt động',exact:true}).click();await page.getByRole('heading',{name:'Nhật ký hoạt động',exact:true}).waitFor();
  await page.getByRole('button',{name:'Pages',exact:true}).click();
  await page.setViewportSize({width:390,height:844});await page.locator('.toast').evaluateAll(values=>values.forEach(value=>value.remove()));await page.screenshot({path:screenshot('mobile.png'),fullPage:true});
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,'Mobile Page cards have no horizontal overflow');
  await page.getByRole('button',{name:'Settings',exact:true}).click();
  await page.getByRole('heading',{name:'Settings',exact:true}).waitFor();
  await page.getByRole('tab',{name:'Kết nối token',exact:true}).click();
  await page.getByRole('article',{name:'Kết nối BM Thương hiệu · Chính',exact:true}).waitFor();
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,'Mobile Settings and token controls remain reachable without overflow');
  assert.ok(await page.locator('.connection-title').evaluateAll(values=>values.every(value=>value.getBoundingClientRect().width>=80)),'Long mobile connection names retain readable width');
  assert.equal(await page.locator('.app-switcher svg').evaluateAll(values=>values.filter(value=>value.getBoundingClientRect().width>0).length),2,'Compact app switcher retains both product icons');
  await page.screenshot({path:screenshot('mobile-settings.png'),fullPage:true});
  // A different configured day boundary must survive both cached GET and the
  // explicit refresh POST. The fixture returns a distinctive UTC count.
  appTimezone='UTC';await page.reload();
  await page.getByRole('tab',{name:'Kết nối token',exact:true,selected:true}).waitFor();
  await page.getByRole('button',{name:'Pages',exact:true}).click();
  await page.getByRole('button',{name:'Xem chi tiết Page The Sunday Studio',exact:true}).click();
  await drawer.getByText('9 bài hôm nay',{exact:true}).waitFor();
  await drawer.getByRole('button',{name:'Cập nhật thông tin & bài viết',exact:true}).click();
  await page.waitForFunction(()=>!document.querySelector('#page-dialog button.button.primary')?.disabled);
  assert.equal(writes.filter(value=>value.path==='/api/pages/90000001/refresh').at(-1).timezone,'UTC','Changed workspace timezone reaches refresh endpoint');
  await drawer.getByText('9 bài hôm nay',{exact:true}).waitFor();
  assert.equal(writes.filter(value=>/publish|invite|feed|schedule|profiles/.test(value.path)).length,0,'Read-only MVP never invokes posting/invitation/Chrome APIs');
  for(const asset of ['app.js','api.js','ui.js','fonts.css','styles.css','workspace-theme.css'])assert.ok(publicPaths.includes(mountBase+'/static/'+asset),'Assets load inside app mount');
  assert.ok(publicPaths.includes(mountBase+'/api/session'));
  assert.ok(writes.every(write=>write.publicPath.startsWith(mountBase+'/api/')),'Secret writes and CSRF stay inside Page Manager namespace');
  if(mountBase)assert.equal(publicPaths.some(path=>path.startsWith('/api/')||path.startsWith('/static/')),false,'Mounted Page Manager never calls Chrome Manager root APIs/assets');
  assert.deepEqual(errors,[]);assert.deepEqual(outside,[]);
  console.log('PASS: '+(mountBase?'mounted /pages/':'standalone')+' Vietnamese Page Manager, canonical multi-token Pages, today coverage/timezone, scoped source, stale detail/catalog guards, token clearing/no storage/no readback, session refresh without secret POST replay, partial sync, app navigation/API namespace isolation and responsive screenshots. Synthetic offline fixtures only.');
 }finally{await browser.close()}
})().catch(error=>{console.error(error);process.exit(1)});
