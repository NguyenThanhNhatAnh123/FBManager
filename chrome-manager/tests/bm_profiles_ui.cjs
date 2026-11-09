const countIs=async(locator,value,message)=>{for(let n=0;n<100;n++){if(await locator.count()===value)return;await new Promise(resolve=>setTimeout(resolve,50))}const result=await locator.count();require('node:assert/strict').equal(result,value,message)};
// Real Chrome render against local fixtures; no BM/Page rights or live accounts are touched.
const {chromium}=require('../frontend/node_modules/playwright');
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const root=path.resolve(__dirname,'..'),dist=path.resolve(process.env.CM_UI_DIST||path.join(root,'frontend/dist')),output=path.join(root,'.runtime/ui');
(async()=>{
 fs.mkdirSync(output,{recursive:true});
 const browser=await chromium.launch({channel:'chrome',headless:true});
 try{
  const page=await browser.newPage({offline:true,serviceWorkers:'block',viewport:{width:1720,height:1050}}),errors=[],calls=[];
  require('./workspace_fixture.cjs').installWorkspaceFixture(page);
  page.on('pageerror',error=>errors.push(error.message));
  const openSettings=async section=>{await page.locator('.sidebar').getByRole('button',{name:'Settings',exact:true}).click();await page.getByRole('tab',{name:section,exact:true}).click()};
  const base={revision:1,proxy_id:null,proxy:null,browser_status:'Stopped',browser_mode:null,browser_error:null,task:null,facebook:null,outlook:null,bm:null};
  const profiles=[{...base,id:1,name:'Regular Profile',profile_path:'fixture/regular'},
   {...base,id:2,name:'BM Master Fixture',profile_path:'fixture/bm',bm:{portfolios:[{bm_id:'123456789012345',name:'Business Demo'}],note:'Profile BM dùng dữ liệu thử',updated_at:new Date().toISOString()}}];
  const proxies=[{id:3,host:'192.0.2.3',port:8080,protocol:'http',status:'Working',ip_address:'2001:db8::3',latency_ms:25,last_checked:null,error_message:null,has_credentials:false,profile_name:'Regular Profile'}];
  profiles[0].proxy_id=3;profiles[0].proxy=proxies[0];
  const privateProxyIds=new Set();
  const profileBusy=row=>['Running','Starting','Queued'].includes(row.browser_status)||row.outlook?.active||['queued','checking'].includes(row.facebook?.status)||['Running','Pending'].includes(row.task?.status);
  const system={cpu_percent:1,memory_percent:20,memory_used_mb:2048,memory_total_mb:16384,memory_available_mb:14336,total_profiles:2,running_browsers:0,queued_browsers:0,stopped_browsers:2,working_proxies:1,failed_proxies:0,total_proxies:1,running_tasks:0,pending_tasks:0,resource_warning:null,max_browsers:5,checking_proxies:0};
  await page.routeWebSocket('**/ws',socket=>socket.close());
  await page.route('http://127.0.0.1:8000/**',async route=>{
   const request=route.request(),p=new URL(request.url()).pathname,method=request.method();
   if(p.startsWith('/api/')){
    let data={};
    if(method!=='GET')calls.push({path:p,method,body:request.postData()?request.postDataJSON():null});
    if(p==='/api/profiles'&&method==='GET')data=profiles;
    else if(p==='/api/proxies')data=proxies.filter(proxy=>!privateProxyIds.has(proxy.id));
    else if(p==='/api/system/status')data=system;
    else if(p==='/api/tasks/history')data={items:[],total:0};
    else if(['/api/tasks','/api/logs'].includes(p))data=[];
    else if(p==='/api/profiles'&&method==='POST'){
     const body=request.postDataJSON(),id=3;
     profiles.push({...base,...body,id,profile_path:'fixture/new-bm',proxy:proxies.find(row=>row.id===body.proxy_id)||null,bm:{mobile_enabled:false,...body.bm,updated_at:new Date().toISOString()}});data={id};
    }else if(/^\/api\/bm-profiles\/\d+\/proxy(?:\/check)?$/.test(p)){
     const row=profiles.find(profile=>profile.id===Number(p.split('/')[3]));
     if(method!=='POST'&&profileBusy(row))return route.fulfill({status:409,json:{detail:'Dừng Chrome và tác vụ trước khi thay hoặc gỡ proxy BM.'}});
     if(method==='PUT'){
      const body=request.postDataJSON();
      const proxy={id:4,host:'192.0.2.50',port:8181,protocol:body.default_protocol,status:'Failed',ip_address:null,latency_ms:null,last_checked:new Date().toISOString(),error_message:'Fixture: proxy check failed',has_credentials:true,profile_name:row.name};
      proxies.push(proxy);privateProxyIds.add(proxy.id);row.proxy_id=proxy.id;row.proxy=proxy;
      data={ok:true,checking:true};
     }else if(method==='POST'){
      row.proxy.status='Working';row.proxy.ip_address='2001:db8::50';row.proxy.latency_ms=18;row.proxy.error_message=null;row.proxy.last_checked=new Date().toISOString();data={ok:true};
     }else if(method==='DELETE'){
      privateProxyIds.delete(row.proxy_id);proxies.splice(proxies.findIndex(proxy=>proxy.id===row.proxy_id),1);row.proxy_id=null;row.proxy=null;data={ok:true};
     }
    }else if(/^\/api\/bm-profiles\/\d+\/mobile$/.test(p)&&method==='PUT'){
     const row=profiles.find(profile=>profile.id===Number(p.split('/')[3]));
     if(profileBusy(row))return route.fulfill({status:409,json:{detail:'Dừng Chrome và tác vụ trước khi đổi chế độ điện thoại.'}});
     row.bm.mobile_enabled=request.postDataJSON().enabled;data={enabled:row.bm.mobile_enabled};
    }else if(/^\/api\/profiles\/\d+$/.test(p)&&method==='PATCH'){
     const row=profiles.find(profile=>profile.id===Number(p.split('/').at(-1)));
     const body=request.postDataJSON();assert.equal(body.revision,row.revision,'Editor sends its captured revision');
     Object.assign(row,body);row.revision++;data={ok:true};
    }else if(/^\/api\/bm-profiles\/\d+$/.test(p)){
     const row=profiles.find(profile=>profile.id===Number(p.split('/').at(-1)));
     if(method==='DELETE'&&row.proxy)return route.fulfill({status:409,json:{detail:'Gỡ proxy riêng trước khi chuyển về Chrome Profiles.'}});
     if(method==='PUT'&&row.proxy)privateProxyIds.add(row.proxy_id);
     row.bm=method==='DELETE'?null:{mobile_enabled:row.bm?.mobile_enabled||false,...request.postDataJSON(),updated_at:new Date().toISOString()};data={ok:true};
    }else if(/^\/api\/bm-profiles\/\d+\/open$/.test(p)){
     const row=profiles.find(profile=>profile.id===Number(p.split('/').at(-2)));
     if(!row.proxy)return route.fulfill({status:409,json:{detail:'Thêm proxy riêng trước khi mở Chrome hoặc BM.'}});
     row.browser_status='Running';row.browser_mode=row.bm?.mobile_enabled?'mobile':'manual';data={status:'opened'};
    }else if(/^\/api\/profiles\/\d+\/(?:start|stop)$/.test(p)){
     const row=profiles.find(profile=>profile.id===Number(p.split('/').at(-2)));
     if(p.endsWith('/start')&&row.bm&&!row.proxy)return route.fulfill({status:409,json:{detail:'Thêm proxy riêng trước khi mở Chrome hoặc BM.'}});
     row.browser_status=p.endsWith('/stop')?'Stopped':'Running';row.browser_mode=p.endsWith('/stop')?null:row.bm?.mobile_enabled?'mobile':'manual';row.outlook=null;data={status:row.browser_status};
    }
    return route.fulfill({json:data});
   }
   const file=path.resolve(dist,'.'+(p==='/'?'/index.html':p));
   if(!file.startsWith(dist+path.sep)||!fs.existsSync(file))return route.abort();
   return route.fulfill({path:file,contentType:file.endsWith('.js')?'application/javascript':file.endsWith('.css')?'text/css':file.endsWith('.woff2')?'font/woff2':'text/html'});
  });
  await page.goto('http://127.0.0.1:8000/');
  await page.getByRole('button',{name:'Dashboard',exact:true}).click();
  await page.getByRole('row').filter({hasText:'Regular Profile'}).waitFor();
  await countIs(page.getByRole('row').filter({hasText:'BM Master Fixture'}),0,'BM profiles stay out of the ordinary profile table');
  await openSettings('BM Profiles');
  await page.getByRole('row').filter({hasText:'BM Master Fixture'}).waitFor();
  await countIs(page.getByRole('row').filter({hasText:'Regular Profile'}),0);
  await page.getByText('Proxy riêng BM dùng cho tất cả website, kể cả khi cài đặt chung chỉ dùng proxy cho Facebook.',{exact:true}).waitFor();
  await page.getByText('Bật Điện thoại khi Chrome đã dừng để lần mở kế tiếp tự dùng khung nhìn, cảm ứng và User-Agent điện thoại, không cần F12.',{exact:true}).waitFor();
  assert.equal(await page.getByRole('button',{name:'Điện thoại của BM Master Fixture',exact:true}).getAttribute('aria-pressed'),'false','Existing BM metadata defaults to mobile disabled');
  const assertNoProxyLaunch=async(name,bmId)=>{
   const row=page.getByRole('row').filter({hasText:name});
   for(const button of [row.getByRole('button',{name:'Mở '+name,exact:true}),row.getByRole('button',{name:'Mở BM',exact:true}),row.getByRole('button',{name:'Mở BM '+bmId+' trong '+name,exact:true})]){
    assert.equal(await button.isDisabled(),true,'BM launch is blocked until its private proxy is assigned');
    assert.equal(await button.getAttribute('title'),'Thêm proxy riêng trước khi mở Chrome hoặc BM');
   }
  };
  await assertNoProxyLaunch('BM Master Fixture','123456789012345');
  await page.screenshot({path:path.join(output,'bm-profiles.png'),fullPage:false});
  await page.getByRole('button',{name:'Thêm profile có sẵn',exact:true}).click();
  let dialog=page.getByRole('dialog');
  await dialog.getByRole('combobox',{name:'Chọn profile cầm BM',exact:true}).selectOption('1');
  await dialog.getByText('Proxy đang gán sẽ được giữ cho profile BM và tách khỏi Proxy Manager thường.',{exact:true}).waitFor();
  await dialog.getByRole('textbox',{name:'Danh sách BM',exact:true}).fill('222222222222222|Business Regular');
  await dialog.getByRole('button',{name:'Thêm vào BM Profiles',exact:true}).click();
  await dialog.waitFor({state:'hidden'});
  await page.getByRole('row').filter({hasText:'Regular Profile'}).waitFor();
  assert.equal(profiles[0].profile_path,'fixture/regular');
  assert.equal(profiles[0].proxy_id,3,'Adopting an existing profile preserves its effective proxy');
  assert.equal(await page.getByRole('button',{name:'Chuyển Regular Profile về Chrome Profiles',exact:true}).isDisabled(),true);
  await openSettings('Proxy Manager');
  await page.getByRole('heading',{name:'Proxy inventory',exact:false}).waitFor();
  await countIs(page.getByText('192.0.2.3:8080',{exact:true}),0,'Adopted BM proxy is excluded from ordinary inventory');
  await openSettings('BM Profiles');
  await page.getByRole('button',{name:'Proxy riêng của Regular Profile',exact:true}).click();
  dialog=page.getByRole('dialog');
  await dialog.getByText('192.0.2.3:8080',{exact:true}).waitFor();
  await dialog.getByRole('button',{name:'Gỡ proxy khỏi profile',exact:true}).click();
  await dialog.waitFor({state:'hidden'});
  await page.waitForFunction(()=>!document.querySelector('[aria-label="Chuyển Regular Profile về Chrome Profiles"]')?.disabled);
  await page.getByRole('button',{name:'Chuyển Regular Profile về Chrome Profiles',exact:true}).click();
  await page.getByRole('row').filter({hasText:'Regular Profile'}).waitFor({state:'hidden'});
  assert.equal(profiles[0].bm,null);assert.equal(profiles[0].profile_path,'fixture/regular');
  await page.getByRole('button',{name:'Tạo profile BM',exact:true}).click();
  dialog=page.getByRole('dialog');
  await dialog.getByRole('textbox',{name:'Tên profile BM',exact:true}).fill('New BM Fixture');
  await countIs(dialog.getByRole('combobox'),0,'New BM profile does not reuse the ordinary proxy pool');
  await dialog.getByText('Tạo Chrome mới hoàn toàn cho BM. Sau khi tạo, thêm Proxy riêng trước khi mở Chrome; proxy này dùng cho tất cả website.',{exact:true}).waitFor();
  await dialog.getByRole('textbox',{name:'Danh sách BM',exact:true}).fill('333333333333333|Business New');
  await dialog.getByRole('textbox',{name:'Ghi chú profile BM',exact:true}).fill('Created with BM classification');
  await dialog.getByRole('button',{name:'Tạo profile BM',exact:true}).click();
  await dialog.waitFor({state:'hidden'});
  await page.getByRole('row').filter({hasText:'New BM Fixture'}).waitFor();
  assert.equal(calls.find(call=>call.path==='/api/profiles'&&call.method==='POST').body.proxy_id,null);
  await assertNoProxyLaunch('New BM Fixture','333333333333333');
  await page.getByRole('button',{name:'Proxy riêng của BM Master Fixture',exact:true}).click();
  dialog=page.getByRole('dialog');
  await dialog.getByText('Proxy BM dùng cho tất cả website, kể cả khi cài đặt chung chỉ dùng proxy cho Facebook.',{exact:true}).waitFor();
  const proxyInput=dialog.locator('input[aria-label="Proxy riêng BM"]');
  assert.equal(await proxyInput.getAttribute('type'),'password','Private proxy credentials are masked while entering');
  assert.equal(await proxyInput.inputValue(),'');
  await dialog.getByRole('combobox',{name:'Giao thức proxy BM',exact:true}).selectOption('socks5');
  await proxyInput.fill(' 192.0.2.50:8181:fixture-user:fixture-password ');
  await dialog.getByRole('button',{name:'Lưu và kiểm tra',exact:true}).click();
  await dialog.waitFor({state:'hidden'});
  await page.getByRole('row').filter({hasText:'BM Master Fixture'}).getByText('192.0.2.50:8181',{exact:true}).waitFor();
  await page.getByRole('row').filter({hasText:'BM Master Fixture'}).getByText('Fixture: proxy check failed',{exact:true}).waitFor();
  assert.deepEqual(calls.find(call=>call.path==='/api/bm-profiles/2/proxy'&&call.method==='PUT').body,{content:'192.0.2.50:8181:fixture-user:fixture-password',default_protocol:'socks5',check_after_save:true});
  const privateProxyId=profiles[1].proxy_id;
  assert.equal(await page.getByRole('button',{name:'Chuyển BM Master Fixture về Chrome Profiles',exact:true}).isDisabled(),true,'Detach private proxy before returning a profile to the ordinary table');
  await page.getByRole('button',{name:'Dashboard',exact:true}).click();
  await countIs(page.getByText('192.0.2.50:8181',{exact:true}),0,'BM proxy stays out of ordinary profiles');
  await openSettings('Proxy Manager');
  await page.getByRole('heading',{name:'Proxy inventory',exact:false}).waitFor();
  await countIs(page.getByText('192.0.2.50:8181',{exact:true}),0,'Private proxy stays out of the ordinary Proxy Manager');
  await openSettings('BM Profiles');
  await page.getByRole('button',{name:'Sửa profile BM Master Fixture',exact:true}).click();
  dialog=page.getByRole('dialog');
  await countIs(dialog.getByRole('combobox'),0,'BM name editor does not expose the ordinary proxy selector');
  await dialog.getByText('Proxy BM riêng: 192.0.2.50:8181. Thay hoặc gỡ bằng nút Proxy riêng trong BM Profiles.',{exact:true}).waitFor();
  await dialog.getByLabel('Profile name',{exact:true}).fill('BM Master Fixture Renamed');
  await dialog.getByRole('button',{name:'Save',exact:true}).click();
  await dialog.waitFor({state:'hidden'});
  assert.equal(calls.find(call=>call.path==='/api/profiles/2'&&call.method==='PATCH').body.proxy_id,privateProxyId,'Renaming preserves private proxy assignment');
  await page.getByRole('button',{name:'Proxy riêng của BM Master Fixture Renamed',exact:true}).click();
  dialog=page.getByRole('dialog');
  assert.equal(await dialog.locator('input[aria-label="Proxy riêng BM"]').inputValue(),'','Saved credentials are never prefilled');
  await countIs(dialog.getByText(/fixture-user|fixture-password/),0);
  await page.evaluate(()=>{const label=document.createElement('p');label.id='fixture-label';label.textContent='FIXTURE ONLY · no live accounts or proxy credentials shown';Object.assign(label.style,{padding:'7px 9px',background:'#182d25',color:'#baf2d0',border:'1px solid #467d61',borderRadius:'6px',font:'12px sans-serif'});document.querySelector('dialog .modal-head h2').insertAdjacentElement('afterend',label)});
  await page.screenshot({path:path.join(output,'bm-private-proxy.png'),fullPage:false});
  await page.locator('#fixture-label').evaluate(element=>element.remove());
  await dialog.getByRole('button',{name:'Kiểm tra proxy hiện tại',exact:true}).click();
  await dialog.getByText('2001:db8::50',{exact:false}).waitFor();
  assert.equal(calls.filter(call=>call.path==='/api/bm-profiles/2/proxy/check'&&call.method==='POST').length,1);
  assert.equal(calls.find(call=>call.path==='/api/bm-profiles/2/proxy/check').body,null,'Checking uses the BM-specific endpoint without credentials');
  await dialog.getByRole('button',{name:'Đóng',exact:true}).click();
  await page.getByRole('button',{name:'Mở BM Master Fixture Renamed',exact:true}).click();
  await page.getByRole('button',{name:'Dừng BM Master Fixture Renamed',exact:true}).waitFor();
  assert.equal(await page.getByRole('button',{name:'Điện thoại của BM Master Fixture Renamed',exact:true}).isDisabled(),true,'Chrome must stop before changing the mobile preset');
  assert.equal(await page.getByRole('button',{name:'Điện thoại của BM Master Fixture Renamed',exact:true}).getAttribute('title'),'Dừng Chrome và tác vụ trước khi đổi chế độ điện thoại');
  await page.getByRole('button',{name:'Proxy riêng của BM Master Fixture Renamed',exact:true}).click();
  dialog=page.getByRole('dialog');
  for(const locator of [dialog.locator('input[aria-label="Proxy riêng BM"]'),dialog.getByRole('combobox',{name:'Giao thức proxy BM',exact:true}),dialog.getByRole('button',{name:'Lưu và kiểm tra',exact:true}),dialog.getByRole('button',{name:'Gỡ proxy khỏi profile',exact:true})])assert.equal(await locator.isDisabled(),true,'Running Chrome blocks proxy mutation');
  assert.equal(await dialog.getByRole('button',{name:'Kiểm tra proxy hiện tại',exact:true}).isEnabled(),true,'Checking remains available while Chrome runs');
  await dialog.getByRole('button',{name:'Kiểm tra proxy hiện tại',exact:true}).click();
  await dialog.getByRole('button',{name:'Đóng',exact:true}).click();
  assert.equal(calls.filter(call=>call.path==='/api/bm-profiles/2/proxy/check'&&call.method==='POST').length,2);
  await page.getByRole('button',{name:'Dừng BM Master Fixture Renamed',exact:true}).click();
  await page.getByRole('button',{name:'Mở BM Master Fixture Renamed',exact:true}).waitFor();
  const mobileToggle=page.getByRole('button',{name:'Điện thoại của BM Master Fixture Renamed',exact:true});
  assert.equal(await mobileToggle.isEnabled(),true);
  await mobileToggle.click();
  await page.waitForFunction(()=>document.querySelector('[aria-label="Điện thoại của BM Master Fixture Renamed"]')?.getAttribute('aria-pressed')==='true');
  assert.deepEqual(calls.find(call=>call.path==='/api/bm-profiles/2/mobile'&&call.method==='PUT').body,{enabled:true});
  assert.equal(profiles[1].bm.mobile_enabled,true);
  await page.reload();
  await openSettings('BM Profiles');
  await page.getByRole('row').filter({hasText:'BM Master Fixture Renamed'}).getByText('Điện thoại · tự mở',{exact:true}).waitFor();
  assert.equal(await page.getByRole('button',{name:'Điện thoại của BM Master Fixture Renamed',exact:true}).getAttribute('aria-pressed'),'true','Saved mobile preset survives a fresh profile response');
  profiles[1].outlook={active:true};
  await page.reload();
  await openSettings('BM Profiles');
  assert.equal(await page.getByRole('button',{name:'Điện thoại của BM Master Fixture Renamed',exact:true}).isDisabled(),true,'Active Outlook task blocks mobile setting changes even when Chrome is stopped');
  await page.getByRole('button',{name:'Proxy riêng của BM Master Fixture Renamed',exact:true}).click();
  dialog=page.getByRole('dialog');
  assert.equal(await dialog.locator('input[aria-label="Proxy riêng BM"]').isDisabled(),true,'An active task blocks mutation even with stopped Chrome');
  assert.equal(await dialog.getByRole('button',{name:'Gỡ proxy khỏi profile',exact:true}).isDisabled(),true);
  assert.equal(await dialog.getByRole('button',{name:'Kiểm tra proxy hiện tại',exact:true}).isEnabled(),true);
  await dialog.getByRole('button',{name:'Đóng',exact:true}).click();
  await page.getByRole('button',{name:'Dừng BM Master Fixture Renamed',exact:true}).click();
  await page.getByRole('button',{name:'Mở BM Master Fixture Renamed',exact:true}).waitFor();
  await page.getByRole('button',{name:'Proxy riêng của BM Master Fixture Renamed',exact:true}).click();
  dialog=page.getByRole('dialog');
  await dialog.getByRole('button',{name:'Gỡ proxy khỏi profile',exact:true}).click();
  await dialog.waitFor({state:'hidden'});
  assert.equal(calls.filter(call=>call.path==='/api/bm-profiles/2/proxy'&&call.method==='DELETE').length,1);
  await page.waitForFunction(()=>!document.querySelector('[aria-label="Chuyển BM Master Fixture Renamed về Chrome Profiles"]')?.disabled);
  assert.equal(profiles[1].proxy,null);assert.equal(profiles[1].proxy_id,null);
  await assertNoProxyLaunch('BM Master Fixture Renamed','123456789012345');
  // Restore the fixture name for the existing metadata/open/search checks below.
  await page.getByRole('button',{name:'Sửa profile BM Master Fixture Renamed',exact:true}).click();
  dialog=page.getByRole('dialog');
  await dialog.getByLabel('Profile name',{exact:true}).fill('BM Master Fixture');
  await dialog.getByRole('button',{name:'Save',exact:true}).click();
  await dialog.waitFor({state:'hidden'});
  await page.getByRole('button',{name:'Sửa thông tin BM của BM Master Fixture',exact:true}).click();
  dialog=page.getByRole('dialog');
  await dialog.getByRole('textbox',{name:'Danh sách BM',exact:true}).fill('444|One\n444|Duplicate');
  const before=calls.length;
  await dialog.getByRole('button',{name:'Lưu thông tin',exact:true}).click();
  await dialog.getByRole('alert').waitFor();assert.equal(calls.length,before);
  await dialog.getByRole('textbox',{name:'Danh sách BM',exact:true}).fill('123456789012345|Business Updated');
  await dialog.getByRole('textbox',{name:'Ghi chú profile BM',exact:true}).fill('Updated locally');
  await dialog.getByRole('button',{name:'Lưu thông tin',exact:true}).click();
  await dialog.waitFor({state:'hidden'});
  await page.getByRole('row').filter({hasText:'Business Updated'}).waitFor();
  // Reassign a fixture proxy before validating BM launch; direct launch remains blocked after detach.
  await page.getByRole('button',{name:'Proxy riêng của BM Master Fixture',exact:true}).click();
  dialog=page.getByRole('dialog');
  await dialog.locator('input[aria-label="Proxy riêng BM"]').fill('192.0.2.50:8181:fixture-user:fixture-password');
  await dialog.getByRole('button',{name:'Lưu và kiểm tra',exact:true}).click();
  await dialog.waitFor({state:'hidden'});
  const bmRow=page.getByRole('row').filter({hasText:'BM Master Fixture'});
  await bmRow.getByRole('button',{name:'Mở BM',exact:true}).click();
  await page.getByRole('button',{name:'Dừng BM Master Fixture',exact:true}).waitFor();
  assert.deepEqual(calls.find(call=>call.path==='/api/bm-profiles/2/open').body,{bm_id:'123456789012345'});
  assert.equal(profiles[1].bm.mobile_enabled,true,'Metadata edits preserve the saved mobile preset');
  await page.getByRole('row').filter({hasText:'BM Master Fixture'}).getByText('Chế độ điện thoại',{exact:true}).waitFor();
  await countIs(page.getByRole('row').filter({hasText:'BM Master Fixture'}).getByText('Automation',{exact:true}),0,'Mobile browsing has its own mode label');
  assert.equal(await page.getByRole('button',{name:'Điện thoại của BM Master Fixture',exact:true}).isDisabled(),true);
  await page.evaluate(()=>{const label=document.createElement('div');label.id='mobile-fixture-label';label.textContent='FIXTURE ONLY · saved mobile preset UI · no live accounts';Object.assign(label.style,{position:'fixed',bottom:'14px',left:'20px',zIndex:99999,padding:'8px 12px',background:'#182d25',color:'#baf2d0',border:'1px solid #467d61',borderRadius:'8px',font:'13px sans-serif'});document.body.appendChild(label)});
  await page.screenshot({path:path.join(output,'bm-mobile-mode.png'),fullPage:false});
  await page.locator('#mobile-fixture-label').evaluate(element=>element.remove());
  await page.getByRole('button',{name:'Dừng BM Master Fixture',exact:true}).click();
  await page.getByRole('button',{name:'Mở BM Master Fixture',exact:true}).waitFor();
  await page.getByRole('button',{name:'Mở BM Master Fixture',exact:true}).click();
  await page.getByRole('row').filter({hasText:'BM Master Fixture'}).getByText('Chế độ điện thoại',{exact:true}).waitFor();
  assert.equal(profiles[1].bm.mobile_enabled,true,'Next Chrome launch keeps the saved mobile preset');
  await page.getByRole('button',{name:'Dừng BM Master Fixture',exact:true}).click();
  await page.getByRole('button',{name:'Mở BM Master Fixture',exact:true}).waitFor();
  await page.getByRole('textbox',{name:'Tìm profile BM',exact:true}).fill('333333333333333');
  await countIs(page.getByRole('row').filter({hasText:'BM Master Fixture'}),0);
  await page.getByRole('row').filter({hasText:'New BM Fixture'}).waitFor();
  await page.getByRole('textbox',{name:'Tìm profile BM',exact:true}).fill('');
  await bmRow.getByRole('button',{name:'Sửa thông tin BM của BM Master Fixture',exact:true}).click();
  dialog=page.getByRole('dialog');
  await dialog.getByRole('textbox',{name:'Danh sách BM',exact:true}).fill('123456789012345|Business Updated\n555555555555555|Second BM');
  await dialog.getByRole('button',{name:'Lưu thông tin',exact:true}).click();
  await dialog.waitFor({state:'hidden'});
  await bmRow.getByText('Chọn nút mở cạnh BM bạn muốn dùng.',{exact:true}).waitFor();
  await countIs(bmRow.getByRole('button',{name:'Mở BM',exact:true}),0,'Multiple BM IDs require choosing a portfolio');
  for(const bmId of ['123456789012345','555555555555555']){
   await Promise.all([
    page.waitForResponse(response=>new URL(response.url()).pathname==='/api/v1/bm-profiles/2/open'),
    bmRow.getByRole('button',{name:'Mở BM '+bmId+' trong BM Master Fixture',exact:true}).click(),
   ]);
  }
  assert.deepEqual(calls.filter(call=>call.path==='/api/bm-profiles/2/open').slice(-2).map(call=>call.body),[{bm_id:'123456789012345'},{bm_id:'555555555555555'}]);
  for(const width of [960,600]){
   await page.setViewportSize({width,height:900});
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,'No page overflow at '+width);
  }
  assert.deepEqual(errors,[]);
  console.log('PASS: separate BM profiles; saved mobile toggle/persistence, busy guards and mode labels; all-website routing note and missing-proxy launch guards; ordinary proxy adoption/exclusion; private masked save/check/detach; BM name-edit preservation; browser/task mutation guards; metadata validation/edit; BM open/stop; search and responsive layout. Fixtures only; mobile fixture validates UI/API state, not browser device emulation. Screenshots: .runtime/ui/bm-private-proxy.png, .runtime/ui/bm-mobile-mode.png');
 }finally{await browser.close()}
})().catch(error=>{console.error(error);process.exit(1)});
