// Disposable offline browser. Every API request is fulfilled by this file.
// No real uploader login, shop check, upload, idea run or Reel mutation.
const {chromium}=require('../../chrome-manager/frontend/node_modules/playwright');
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const root=path.resolve(__dirname,'..'),frontend=path.join(root,'frontend');
const origin='http://127.0.0.1:8000',base='/pages',copy=value=>JSON.parse(JSON.stringify(value));
const username='offline-tool-user',password='OFFLINE_PASSWORD_NOT_REAL';
(async()=>{
 const browser=await chromium.launch({channel:'chrome',headless:true});
 try{
  const context=await browser.newContext({offline:true,serviceWorkers:'block',viewport:{width:1500,height:1050}});
  const page=await context.newPage(),reads=[],writes=[],outside=[],errors=[];
  let sessions=0,releaseConnect=null,connecting=false,releaseCheck=null,localCheck='success';
  const csrf=()=>`fixture-uploader-${sessions}`;
  const local={installed:true,tool_root:'PODSach bulk prep',available:true,configured_blueox:true,
    active_domain:'blueox179.com',authenticated:true,source:'zitat25',ready:true,blockers:[],endpoint:'http://127.0.0.1:8770'};
  const web={installed:true,tool_root:'PODSach web',available:false,configured_blueox:false,
    active_domain:null,authenticated:null,source:null,ready:false,blockers:['Chưa kết nối web.'],
    session_saved:false,endpoint:'https://ul.eagleglobal2023.com'};
  const upload=()=>({...copy(local),provider:'local',policy:'local_first',providers:{local:copy(local),web:copy(web)}});
  const pipeline=()=>({pipeline:{directory:'C:\\Users\\Admin\\Idea',settings:{enabled:false,
    campaign_source:{shop:'blueox179.com',campaign_url:'https://blueox179.com/campaign/zitat25',verified:true},
    black_campaign_source:{shop:'blueox179.com',campaign_url:'https://blueox179.com/campaign/ge2610',verified:true}},
    ready:false,idea_count:0,ideas:[],jobs:[],paged:true,summary:{idea_count:0},
    pagination:{total:0,limit:12,offset:0}},upload:upload(),
    video_agent:{status:'ready',title:'Offline fixture video agent',blockers:[]},ranking:{mode:'preview_only',cohorts:[]},
    execution:{mode:'manual',supported:false,blockers:[]},runs:{items:[]}});
  await page.addInitScript(()=>{const original=window.setInterval;window.setInterval=(fn,delay,...args)=>delay===10000?(window.fixtureUploaderPoll=fn,original(()=>{},delay)):original(fn,delay,...args)});
  await page.route('**/*',async route=>{
   const request=route.request(),url=new URL(request.url());
   if(url.origin===origin&&url.pathname==='/auth/api/me')return route.fulfill({json:{user:{id:1,username:'fixture',role:'admin',must_change_password:false},csrf_token:'fixture-via-csrf'}});
   if(url.origin===origin&&/^(?:\/pages)?\/api\//.test(url.pathname)&&!['GET','HEAD','OPTIONS'].includes(request.method()))assert.equal(request.headers()['x-via-csrf'],'fixture-via-csrf');

   if(url.origin!==origin||!url.pathname.startsWith(base+'/')){outside.push(request.url());return route.abort()}
   const p=url.pathname.slice(base.length),method=request.method(),body=request.postData()?request.postDataJSON():null;
   if(p.startsWith('/api/')){
    if(method==='GET')reads.push(p);else{writes.push({path:p,method,body});assert.equal(request.headers()['x-mpm-csrf'],csrf())}
    if(p==='/api/session'){sessions++;return route.fulfill({json:{csrf_token:csrf(),app:{name:'Page Manager',timezone:'America/Los_Angeles',idea_pipeline:true}}})}
    if(p==='/api/overview')return route.fulfill({json:{stats:{pages:0},updated_at:'2030-01-01T00:00:00Z'}});
    if(['/api/connections','/api/connections/options','/api/pages','/api/events'].includes(p))return route.fulfill({json:{items:[],total:0,limit:10,offset:0}});
    if(p==='/api/idea-pipeline'&&method==='GET')return route.fulfill({json:pipeline()});
    if(p==='/api/idea-pipeline/upload/status'&&method==='GET')return route.fulfill({json:upload()});
    if(p==='/api/idea-pipeline/upload/connect'){
     assert.equal(method,'POST');assert.deepEqual(body,{username,password});assert.equal(connecting,false,'Connect double-click cannot send a second login');connecting=true;
     await new Promise(resolve=>{releaseConnect=resolve});
     Object.assign(web,{available:true,configured_blueox:true,active_domain:'blueox179.com',authenticated:null,
       source:'zitat25',session_saved:true,ready:false,blockers:['Kiểm tra phiên shop.']});
     connecting=false;return route.fulfill({json:{upload:upload()}});
    }
    if(p==='/api/idea-pipeline/upload/session-check'){
     assert.equal(method,'POST');assert.ok(['local','web'].includes(body?.provider));
     if(body.provider==='web'){
      if(!web.session_saved)return route.fulfill({status:409,json:{code:'uploader_check_web_not_connected',detail:'PRIVATE COOKIE RAW ERROR'}});
      Object.assign(web,{authenticated:true,ready:true,blockers:[]});return route.fulfill({json:{upload:upload(),check:{provider:'web',authenticated:true,ready:true,status:'authenticated',reason:null,checked_at:'2030-01-01T00:00:00Z'}}});
     }
     if(localCheck==='success')await new Promise(resolve=>{releaseCheck=resolve});
     if(localCheck==='wrong_shop')return route.fulfill({status:409,json:{code:'uploader_check_wrong_shop',detail:'PRIVATE COOKIE RAW ERROR'}});
     if(localCheck==='offline')return route.fulfill({status:503,json:{code:'uploader_check_tool_unavailable',detail:'PRIVATE COOKIE RAW ERROR'}});
     const ok=localCheck!=='negative';Object.assign(local,{authenticated:ok,ready:ok,blockers:ok?[]:['Phiên shop chưa hợp lệ.']});
     return route.fulfill({json:{upload:upload(),check:{provider:'local',authenticated:ok,ready:ok,status:ok?'authenticated':'authentication_required',reason:ok?null:'authentication_required',checked_at:'2030-01-01T01:00:00Z'}}});
    }
    if(p==='/api/idea-pipeline/upload/connection'){
     assert.equal(method,'DELETE');assert.equal(body,null);
     Object.assign(web,{session_saved:false,available:false,authenticated:null,ready:false,blockers:['Chưa kết nối web.']});
     return route.fulfill({json:{upload:upload()}});
    }
    throw new Error('Unexpected API in focused uploader fixture: '+method+' '+p);
   }
   if(p==='/')return route.fulfill({path:path.join(frontend,'index.html'),contentType:'text/html'});
   if(p.startsWith('/static/')){
    const file=path.resolve(frontend,p.slice(8));if(!file.startsWith(frontend+path.sep)||!fs.existsSync(file))return route.abort();
    return route.fulfill({path:file,contentType:file.endsWith('.js')?'application/javascript':file.endsWith('.woff2')?'font/woff2':'text/css'});
   }
   return route.abort();
  });
  page.on('pageerror',error=>errors.push(error.message));page.setDefaultTimeout(10000);
  await page.goto(origin+base+'/');await page.getByRole('button',{name:'Settings',exact:true}).click();
  await page.getByRole('tab',{name:'Kho nội dung',exact:true}).click();
  const area=page.getByRole('tabpanel',{name:'Kho nội dung',exact:true});
  await area.getByRole('tab',{name:'Kết nối',exact:true}).click();
  const controls=()=>area.locator('.idea-uploader-controls'),details=()=>area.locator('.idea-uploader-connect');
  const user=()=>area.getByLabel('Tài khoản tool web',{exact:true}),secret=()=>area.getByLabel('Mật khẩu tool web',{exact:true});
  const provider=kind=>area.locator('.idea-uploader-provider').filter({hasText:kind==='local'?'Cục bộ · 8770':'Web dự phòng'});
  const openLogin=async()=>{if(!await details().evaluate(node=>node.open))await details().locator('summary').click()};
  await controls().waitFor();assert.equal(await controls().count(),1);
  assert.equal(await details().evaluate(node=>node.open),false,'Login is collapsed by default');
  assert.equal(await secret().getAttribute('type'),'password');
  assert.equal(await provider('local').getByRole('link',{name:'Mở tool',exact:true}).getAttribute('href'),'http://127.0.0.1:8770');
  assert.equal(await provider('web').getByRole('link',{name:'Mở tool',exact:true}).getAttribute('href'),'https://ul.eagleglobal2023.com/s/blueox179.com/');
  assert.equal(await provider('web').getByRole('button',{name:'Kiểm tra phiên web',exact:true}).isDisabled(),false,'Diagnostic remains available before connection');
  assert.equal(await area.getByRole('region',{name:'Kết nối AI producer'}).count(),1);
  assert.equal(writes.length,0,'Opening connection UI does not log in or check a shop');
  await openLogin();await user().fill(username);await secret().fill(password);
  await user().evaluate(node=>{window.fixtureUploaderUserNode=node});
  local.source='changed-source-for-read-poll';const beforePoll=reads.length;
  await page.evaluate(()=>window.fixtureUploaderPoll());await page.waitForFunction(()=>window.fixtureUploaderUserNode?.isConnected);
  assert.ok(reads.length>beforePoll);
  assert.equal(await user().inputValue(),username);assert.equal(await secret().inputValue(),password);
  assert.equal(await user().evaluate(node=>window.fixtureUploaderUserNode===node),true,'Polling preserves typed credential DOM');
  assert.equal(await page.evaluate(()=>localStorage.length+sessionStorage.length),0);
  assert.equal(writes.length,0,'Polling never sends tool credentials or shop checks');
  await area.getByRole('button',{name:'Kết nối web',exact:true}).evaluate(node=>{node.click();node.click()});
  await page.waitForFunction(()=>[...document.querySelectorAll('button')].some(node=>node.textContent==='Đang kết nối…'));
  assert.equal(await secret().inputValue(),'','Password clears immediately when the login request starts');
  assert.equal(await controls().count(),1,'Connect does not append duplicate provider cards');
  assert.equal(writes.length,1);assert.equal(writes[0].path,'/api/idea-pipeline/upload/connect');
  while(!releaseConnect)await page.waitForTimeout(20);releaseConnect();releaseConnect=null;
  await area.getByText('Đã kết nối tool web. Kiểm tra phiên shop nếu trạng thái còn yêu cầu.',{exact:true}).waitFor();
  await provider('web').getByText('Đã kết nối · cần kiểm tra shop',{exact:true}).waitFor();
  assert.match(await provider('local').innerText(),/Đang chọn/);assert.doesNotMatch(await provider('web').innerText(),/Đang chọn/);
  assert.equal(await controls().count(),1);assert.equal(await secret().inputValue(),'');
  await page.evaluate(()=>window.fixtureUploaderPoll());assert.equal(writes.length,1,'Connected-state polling does not check a shop automatically');
  await provider('web').getByRole('button',{name:'Kiểm tra phiên web',exact:true}).click();
  await provider('web').getByText('Sẵn sàng',{exact:true}).waitFor();assert.equal(writes.length,2);
  assert.deepEqual(writes[1],{path:'/api/idea-pipeline/upload/session-check',method:'POST',body:{provider:'web'}});
  assert.match(await provider('local').innerText(),/Đang chọn/);assert.equal(await controls().count(),1);
  await page.evaluate(()=>window.fixtureUploaderPoll());assert.equal(writes.length,2);
  await openLogin();await area.getByRole('button',{name:'Ngắt kết nối web',exact:true}).click();
  await area.getByText('Đã ngắt phiên tool web trên máy này.',{exact:true}).waitFor();
  assert.equal(writes.length,3);assert.deepEqual(writes[2],{path:'/api/idea-pipeline/upload/connection',method:'DELETE',body:null});
  assert.equal(await controls().count(),1);assert.equal(await secret().inputValue(),'');
  assert.equal(await area.getByRole('button',{name:'Ngắt kết nối web',exact:true}).count(),0);
  assert.equal(await provider('web').getByRole('button',{name:'Kiểm tra phiên web',exact:true}).isDisabled(),false);
  await provider('local').getByRole('button',{name:'Kiểm tra phiên cục bộ',exact:true}).evaluate(node=>{node.click();node.click()});
  await provider('local').getByRole('status').filter({hasText:'Đang kiểm tra đúng shop'}).waitFor();
  assert.equal(writes.length,4,'Only one explicit local check is submitted');
  assert.equal(await provider('local').getByRole('button',{name:'Kiểm tra phiên cục bộ',exact:true}).isDisabled(),true);
  assert.equal(await provider('web').getByRole('button',{name:'Kiểm tra phiên web',exact:true}).isDisabled(),true);
  while(!releaseCheck)await page.waitForTimeout(20);releaseCheck();releaseCheck=null;
  await provider('local').getByText('Lần kiểm tra gần nhất: Phiên shop Blueox hợp lệ.',{exact:true}).waitFor();
  assert.deepEqual(writes[3],{path:'/api/idea-pipeline/upload/session-check',method:'POST',body:{provider:'local'}});
  assert.equal(await controls().count(),1);
  localCheck='negative';await provider('local').getByRole('button',{name:'Kiểm tra phiên cục bộ',exact:true}).click();
  await provider('local').getByText('Lần kiểm tra gần nhất: Phiên shop Blueox chưa hợp lệ. Mở tool để đăng nhập rồi kiểm tra lại.',{exact:true}).waitFor();
  assert.equal(writes.length,5);assert.equal(await provider('local').getByText('Phiên shop chưa hợp lệ',{exact:true}).count(),1);
  localCheck='wrong_shop';await provider('local').getByRole('button',{name:'Kiểm tra phiên cục bộ',exact:true}).click();
  await provider('local').getByText('Lần kiểm tra gần nhất: Tool đang chọn shop khác. Chọn blueox179.com trong tool rồi kiểm tra lại.',{exact:true}).waitFor();
  assert.equal(writes.length,6);
  localCheck='offline';Object.assign(local,{available:false,authenticated:null,ready:false,blockers:['Chưa mở tool.']});
  await provider('local').getByRole('button',{name:'Kiểm tra phiên cục bộ',exact:true}).click();
  await provider('local').getByText('Lần kiểm tra gần nhất: Chưa kết nối được tool này. Mở tool rồi kiểm tra lại.',{exact:true}).waitFor();
  assert.equal(writes.length,7);
  await provider('web').getByRole('button',{name:'Kiểm tra phiên web',exact:true}).click();
  await provider('web').getByText('Lần kiểm tra gần nhất: Chưa kết nối tool web. Dùng Kết nối web dự phòng bên dưới.',{exact:true}).waitFor();
  assert.equal(writes.length,8);assert.equal(writes.filter(row=>row.path.endsWith('/connect')).length,1,'Checking never logs in automatically');
  assert.equal((await area.innerText()).includes('PRIVATE'),false,'Session-check error details never reach the DOM');
  assert.equal(await page.evaluate(()=>localStorage.length+sessionStorage.length),0);
  assert.equal((await area.innerText()).includes(password),false);
  await page.setViewportSize({width:600,height:1000});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
  assert.deepEqual(errors,[]);assert.deepEqual(outside,[]);
  assert.ok(writes.every(row=>row.path.startsWith('/api/idea-pipeline/upload/')));
   console.log('PASS: mounted offline uploader UI; explicit local/web check, pending double-click guard, per-provider success/negative/safe reason, disconnected diagnostics, collapsed masked login, polling preserves credentials without probes, producer mount without extra calls, no duplicate cards/storage/live network.');
 }finally{await browser.close()}
})().catch(error=>{console.error(error);process.exit(1)});
