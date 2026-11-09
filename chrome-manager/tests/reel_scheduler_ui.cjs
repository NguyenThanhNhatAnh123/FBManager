const countIs=async(locator,value,message)=>{for(let n=0;n<100;n++){if(await locator.count()===value)return;await new Promise(resolve=>setTimeout(resolve,50))}const result=await locator.count();require('node:assert/strict').equal(result,value,message)};
// Fresh disposable Chrome with local fixtures only. No real upload, schedule or publishing.
const {chromium}=require('../frontend/node_modules/playwright');
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const root=path.resolve(__dirname,'..'),dist=path.resolve(process.env.CM_UI_DIST||path.join(root,'frontend/dist')),output=path.join(root,'.runtime/ui');
function semanticRequest(body){const {request_id,...semantic}=body;assert.match(request_id,/^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/);return semantic}
(async()=>{
 fs.mkdirSync(output,{recursive:true});
 const browser=await chromium.launch({channel:'chrome',headless:true});
 try{
  const page=await browser.newPage({offline:true,serviceWorkers:'block',viewport:{width:1720,height:1100}}),errors=[],calls=[],unexpected=[],listRequests=[];
  const mediaRequests=[];page.on('request',request=>{const pathname=new URL(request.url()).pathname;if(/^\/api\/(?:v1\/)?reels\/assets(?:\/|$)/.test(pathname))mediaRequests.push({path:pathname,method:request.method()})});
  require('./workspace_fixture.cjs').installWorkspaceFixture(page);
  page.on('pageerror',error=>errors.push(error.message));
  const now=()=>new Date().toISOString(),assetId='a'.repeat(32),savedId='b'.repeat(32);
  const video=fs.readFileSync(path.join(__dirname,'fixtures/saved-schedule-preview.mp4')); // Synthetic generated video, no user media.
  const proxy={id:3,host:'192.0.2.3',port:8080,protocol:'http',status:'Working',ip_address:'192.0.2.3',latency_ms:1,last_checked:null,error_message:null,has_credentials:false};
  const base={proxy_id:null,proxy:null,browser_status:'Stopped',browser_mode:null,browser_error:null,task:null,facebook:null,outlook:null,bm:null};
  const profiles=[{...base,id:1,name:'Reel Fixture 001',profile_path:'fixture/one'},{...base,id:3,name:'Reel Fixture 003',profile_path:'fixture/three',proxy_id:3,proxy},{...base,id:101,name:'BM Fixture',profile_path:'fixture/bm',bm:{portfolios:[],note:'',updated_at:now()}}];
  const assets=[{id:savedId,name:'Saved fixture.mov',size:2048,mime:'video/quicktime',created_at:now(),preview_url:'/api/reels/assets/'+savedId+'/video'}],jobs=[],requests=new Map();
  let loseFirstCreateResponse=true;
  let uploadMode='cancel',releaseUpload=null,verifyMode='success',releaseVerification=null;
  function eligibility(value){const known=['cached-fixture','received-fixture','manual-fixture','unedited-fixture'].find(name=>value==='https://www.facebook.com/'+name),ready=!!known&&known!=='unedited-fixture';return {page_id:known?'555':null,posting_page_id:known?'555':null,can_publish:ready,information_status:ready?'updated':'not_updated',posting_block_reason:ready?null:known?'page_information_required':'page_identity_unverified',posting_revision:ready?1:0}}
  function makeJob(id,body,changes={}){
   const asset=assets.find(item=>item.id===body.asset_id);
   return {id,profile_id:body.profile_id,profile_name:profiles.find(item=>item.id===body.profile_id).name,page_url:body.page_url,page_name:body.page_name||'Page Fixture',asset_id:body.asset_id,asset_name:asset.name,asset_size:asset.size,caption:body.caption,scheduled_at:body.fold===1?'2030-11-03T09:30:00Z':'2030-11-05T01:45:00Z',timezone:body.timezone,status:'scheduled',step:'scheduled',message:'Fixture: lịch đã lưu.',created_at:now(),updated_at:now(),started_at:null,finished_at:null,published_url:null,verification_url:null,publish_attempted:false,can_verify_link:false,can_cancel:true,can_resume:false,can_archive:false,archived_at:null,can_restore:false,...changes};
  }
  const system={cpu_percent:1,memory_percent:20,memory_used_mb:2048,memory_total_mb:16384,memory_available_mb:14336,total_profiles:3,running_browsers:0,queued_browsers:0,stopped_browsers:3,working_proxies:1,failed_proxies:0,total_proxies:1,running_tasks:0,pending_tasks:0,resource_warning:null,max_browsers:5,checking_proxies:0};
  let workspaceSocket,revision=0;
  const sendRevision=()=>workspaceSocket.send(JSON.stringify({kind:'changed',system,workspace_revision:++revision}));
  await page.routeWebSocket('**/ws*',socket=>{workspaceSocket=socket;sendRevision()});
  const invalidateSaved=()=>Promise.all([
   page.waitForResponse(response=>new URL(response.url()).pathname==='/api/v1/reels'&&response.request().method()==='GET'),
   Promise.resolve().then(sendRevision),
  ]);
  await page.route('**/*',async route=>{
   const request=route.request(),url=new URL(request.url());
   if(url.protocol==='blob:')return route.continue();
   if(url.origin!=='http://127.0.0.1:8000'){unexpected.push(url.origin);return route.abort()}
   const p=url.pathname,method=request.method();
   if(p.startsWith('/api/')){
    const binary=p==='/api/reels/assets'&&method==='POST',body=binary?null:request.postData()?request.postDataJSON():null;
    if(method!=='GET')calls.push({path:p,method,body,headers:request.headers(),bytes:binary?request.postDataBuffer():null});
    let data;
    if(p==='/api/session')data={};
    else if(p==='/api/profiles')data=profiles;
    else if(p==='/api/proxies')data=[proxy];
    else if(p==='/api/system/status')data=system;
    else if(p==='/api/tasks/history')data={items:[],total:0};
    else if(['/api/tasks','/api/logs'].includes(p))data=[];
    else if(p==='/api/reels/config')data={max_upload_bytes:1073741824,accepted_extensions:['.mp4','.m4v','.mov'],default_timezone:'America/Los_Angeles',scheduler_running:true,background_browser:true};
    else if(p==='/api/reels/pages')data=url.searchParams.get('profile_id')==='3'?[{page_url:'https://www.facebook.com/cached-fixture',page_name:'Cached Page Fixture',source:'facebook_scan'},{page_url:'https://www.facebook.com/received-fixture',page_name:'Received Page Fixture',source:'received'},{page_url:'https://www.facebook.com/unedited-fixture',page_name:'New Page Fixture',source:'received'}].map(item=>({...item,...eligibility(item.page_url)})):[];
    else if(p==='/api/reels/page-eligibility')data=eligibility(url.searchParams.get('page_url'));
    else if(p==='/api/reels/assets'&&method==='GET')data=assets;
    else if(binary){
     if(uploadMode==='cancel'){
      await new Promise(resolve=>{releaseUpload=resolve});return route.abort().catch(()=>{});
     }
     const asset={id:assetId,name:decodeURIComponent(request.headers()['x-filename']),size:video.length,mime:'video/mp4',created_at:now(),preview_url:'/api/reels/assets/'+assetId+'/video'};
     assets.push(asset);return route.fulfill({status:201,json:asset});
    }else if(/^\/api\/reels\/assets\/[a-f0-9]+\/video$/.test(p))return route.fulfill({body:video,contentType:'video/mp4',headers:{'Accept-Ranges':'bytes'}});
    else if(p==='/api/reels'&&method==='GET'){listRequests.push(url.search);data=url.searchParams.get('include_archived')==='true'?jobs:jobs.filter(job=>!job.archived_at)}
    else if(p==='/api/reels'&&method==='POST'){
     if(body.scheduled_local==='2030-11-03T01:30'&&body.fold===undefined)return route.fulfill({status:422,json:{detail:'Giờ đăng bị trùng do DST. Chọn lần đầu hoặc lần thứ hai.'}});
     const previous=requests.get(body.request_id);
     if(previous){assert.deepEqual(previous.body,body);data=previous.job}
     else{const job=makeJob(jobs.length+1,body);jobs.push(job);requests.set(body.request_id,{body,job});data=job}
     if(loseFirstCreateResponse){loseFirstCreateResponse=false;return route.abort('failed')}
    }else if(/^\/api\/reels\/\d+\/verify-link$/.test(p)&&method==='POST'){
     const job=jobs.find(item=>item.id===Number(p.split('/')[3]));
     assert.ok(job.can_verify_link&&job.publish_attempted&&!job.archived_at&&job.status==='needs_review','Verification targets only server-approved paused attempted jobs');
     assert.match(body.reel_url,/^https:\/\/www\.facebook\.com\/reel\/\d+$/);
     if(verifyMode==='reject')return route.fulfill({status:409,json:{detail:'Fixture: chưa thấy video tại link Reel.'}});
     if(verifyMode==='hold')await new Promise(resolve=>{releaseVerification=resolve});
     Object.assign(job,{verification_url:body.reel_url,status:'verifying',step:'verifying',can_verify_link:false,can_resume:false,can_cancel:true,message:'Fixture: chỉ kiểm tra video tại link Reel, không đăng thêm video.'});data={ok:true};
    }else if(/^\/api\/reels\/\d+\/(cancel|resume|archive|restore)$/.test(p)&&method==='POST'){
     const job=jobs.find(item=>item.id===Number(p.split('/')[3]));
     if(p.endsWith('/archive')){
      if(!job.can_archive||job.status!=='cancelled'||job.publish_attempted)return route.fulfill({status:409,json:{detail:'Fixture: lịch chưa thể ẩn.'}});
      Object.assign(job,{archived_at:now(),can_archive:false,can_restore:true,can_resume:false});
     }else if(p.endsWith('/restore')){
      if(!job.can_restore||!job.archived_at)return route.fulfill({status:409,json:{detail:'Fixture: lịch chưa thể khôi phục.'}});
      Object.assign(job,{archived_at:null,status:'cancelled',step:'cancelled',can_archive:true,can_restore:false,can_resume:false,can_cancel:false});
     }else Object.assign(job,p.endsWith('/cancel')?{status:'cancelled',step:'cancelled',can_cancel:false,can_resume:false,can_archive:true,message:'Fixture: đã hủy lịch.'}:{status:'verifying',step:'verifying',can_cancel:true,can_resume:false,can_archive:false,message:'Fixture: đang kiểm tra bài đã gửi trước khi tiếp tục.'});data={ok:true};
    }else return route.fulfill({status:404,json:{detail:'Unknown fixture API'}});
    return route.fulfill({json:data});
   }
   const file=path.resolve(dist,'.'+(p==='/'?'/index.html':p));
   if(!file.startsWith(dist+path.sep)||!fs.existsSync(file))return route.abort();
   return route.fulfill({path:file,contentType:file.endsWith('.js')?'application/javascript':file.endsWith('.css')?'text/css':file.endsWith('.woff2')?'font/woff2':'text/html'});
  });
  await page.goto('http://127.0.0.1:8000/');
  await page.getByRole('button',{name:'Dashboard',exact:true}).click();
  await page.getByRole('checkbox',{name:'Select Reel Fixture 003',exact:true}).check();
  await page.getByRole('button',{name:'Lịch đăng Page',exact:true}).click();
  const form=page.locator('.reels-form-panel > form.reels-form');
  const nav=page.getByRole('navigation',{name:'Điều hướng chính',exact:true});
  const openCreate=()=>nav.getByRole('button',{name:'Lịch đăng Page',exact:true}).click();
  const openSaved=async()=>{await nav.getByRole('button',{name:'Lịch đã lưu',exact:true}).click();await page.getByLabel('Lọc thời gian lịch Reel',{exact:true}).selectOption('all')};
  const assertDraft=async expected=>{await closeDetails();const before=calls.length;await openCreate();assert.equal(await form.getByRole('textbox',{name:'Caption Reel',exact:true}).inputValue(),expected,'The scheduling draft survives saved-list navigation');await openSaved();assert.equal(calls.length,before,'Switching between create and saved never writes data')};
  const detail=page.getByRole('dialog');
  const card=id=>page.getByRole('article',{name:'Lịch Reel #'+id,exact:true});
  const closeDetails=async()=>{if(await detail.count()){await detail.getByRole('button',{name:'Close dialog',exact:true}).click();await detail.waitFor({state:'hidden'})}};
  const openDetails=async id=>{await closeDetails();await card(id).getByRole('button',{name:'Xem chi tiết lịch Reel #'+id,exact:true}).click();await detail.waitFor();return detail};
  const refreshList=async()=>{await closeDetails();await page.getByRole('button',{name:'Làm mới lịch',exact:true}).click()};
  assert.deepEqual(await nav.getByRole('button').allTextContents(),['Dashboard','Pages','Lịch đăng Page','Lịch đã lưu'],'Saved schedules appear directly below scheduling');
  await form.getByRole('combobox',{name:'Profile đăng Reel',exact:true}).waitFor();
  await countIs(page.getByRole('region',{name:'Danh sách lịch Reel',exact:true}),0,'The create page does not duplicate the saved list');
  assert.equal(await form.getByRole('combobox',{name:'Profile đăng Reel',exact:true}).inputValue(),'3','Dashboard profile selection carries into scheduling');
  await countIs(form.getByRole('option',{name:/BM Fixture/}),0);
  await form.getByRole('option',{name:'Received Page Fixture · Page đã nhận · Đã đổi thông tin',exact:true}).waitFor({state:'attached'});
  assert.equal(await form.getByLabel('Múi giờ đăng Reel',{exact:true}).inputValue(),'America/Los_Angeles');
  await page.getByText('Giữ máy và server đang chạy. Khi mở lại server, lịch quá giờ sẽ được đưa vào xử lý.',{exact:true}).waitFor();
  assert.equal(calls.length,0,'Opening the scheduler does not create schedules or upload');
  const input=form.getByLabel('Chọn video Reel',{exact:true});
  await input.setInputFiles({name:'invalid.txt',mimeType:'text/plain',buffer:Buffer.from('fixture')});
  await form.getByRole('alert').getByText('Chọn video .mp4, .m4v, .mov.',{exact:true}).waitFor();
  assert.equal(calls.length,0,'Invalid file extension is rejected before upload');
  await input.setInputFiles({name:'cancel.mp4',mimeType:'video/mp4',buffer:video});
  await page.waitForFunction(()=>document.querySelector('video[aria-label="Xem trước video Reel"]')?.getAttribute('src')?.startsWith('blob:'));
  await form.getByRole('button',{name:'Tải video lên Manager',exact:true}).click();
  await form.getByRole('progressbar',{name:'Tiến trình tải video lên',exact:true}).waitFor();
  await form.getByRole('button',{name:'Hủy tải video',exact:true}).click();
  await form.getByText('Đã hủy tải video lên.',{exact:true}).waitFor();
  if(releaseUpload){releaseUpload();releaseUpload=null}
  assert.equal(assets.length,1,'Cancelled upload does not select or create a fixture asset');
  uploadMode='complete';
  const filename='Mẫu Reel thử.mp4';
  await input.setInputFiles({name:filename,mimeType:'video/mp4',buffer:video});
  await form.getByRole('button',{name:'Tải video lên Manager',exact:true}).click();
  await page.waitForFunction(id=>document.querySelector('select[aria-label="Video đã lưu"]')?.value===id,assetId);
  const uploadCall=calls.filter(call=>call.path==='/api/reels/assets').at(-1);
  assert.equal(uploadCall.headers['content-type'],'video/mp4');assert.equal(uploadCall.headers['x-filename'],encodeURIComponent(filename));
  assert.ok(uploadCall.bytes.equals(video),'Video is sent as raw File bytes, not JSON/base64');
  await page.waitForFunction(id=>document.querySelector('.reels-form-panel video')?.getAttribute('src')==='/api/v1/reels/assets/'+id+'/video',assetId);
  assert.equal(await form.locator('video').getAttribute('src'),'/api/v1/reels/assets/'+assetId+'/video');
  assert.equal(await form.getByRole('combobox',{name:'Page đăng Reel',exact:true}).locator('option[value="https://www.facebook.com/unedited-fixture"]').evaluate(option=>option.disabled),true,'A received Page without verified information cannot be selected for scheduling');
  assert.equal(calls.filter(call=>call.path==='/api/reels').length,0,'Listing blocked Pages never creates a schedule');
  await form.getByRole('combobox',{name:'Page đăng Reel',exact:true}).selectOption('https://www.facebook.com/received-fixture');
  const caption='Caption Reel fixture\nGiữ nguyên nội dung và ký tự. #fixture';
  await form.getByRole('textbox',{name:'Caption Reel',exact:true}).fill(caption);
  await form.getByLabel('Ngày giờ đăng Reel',{exact:true}).fill('2030-11-05T08:45');
  await form.getByLabel('Múi giờ đăng Reel',{exact:true}).fill('Asia/Ho_Chi_Minh');
  await form.getByText('05/11/2030 08:45 · Asia/Ho_Chi_Minh',{exact:true}).waitFor();
  assert.equal(calls.filter(call=>call.path==='/api/reels').length,0,'Editing caption/time does not create a schedule');
  const beforeNavigation=calls.length;
  await openSaved();await page.getByRole('heading',{name:'Lịch đã lưu',exact:true}).waitFor();
  await countIs(form,0,'The saved-list page does not duplicate the scheduling form');
  await openCreate();
  assert.equal(await form.getByRole('textbox',{name:'Caption Reel',exact:true}).inputValue(),caption);
  assert.equal(await form.getByLabel('Ngày giờ đăng Reel',{exact:true}).inputValue(),'2030-11-05T08:45');
  assert.equal(await form.getByLabel('Múi giờ đăng Reel',{exact:true}).inputValue(),'Asia/Ho_Chi_Minh');
  assert.equal(await form.getByRole('combobox',{name:'Video đã lưu',exact:true}).inputValue(),assetId);
  assert.equal(calls.length,beforeNavigation,'Read-only navigation preserves every draft field without creating or uploading');
  await form.getByRole('button',{name:'Đặt lịch đăng Reel',exact:true}).click();
  await form.getByRole('alert').waitFor();
  assert.equal(jobs.length,1,'The first request committed despite its lost response');
  await form.getByRole('button',{name:'Đặt lịch đăng Reel',exact:true}).click();
  await form.getByText('Đã lưu lịch. Đến giờ, Manager sẽ chạy Chrome ngầm bằng đúng profile và đăng Reel lên Page đã chọn.',{exact:true}).waitFor();
  const firstCreates=calls.filter(call=>call.path==='/api/reels');
  assert.equal(firstCreates.length,2);assert.equal(firstCreates[0].body.request_id,firstCreates[1].body.request_id);
  assert.equal(jobs.length,1,'Retrying an uncertain response reuses the original request and job');
  await openSaved();
  const first=card(1);await first.waitFor();
  assert.equal((await first.innerText()).replace(/\s+/g,' ').trim(),('Received Page Fixture 05/11/2030 08:45 Asia/Ho_Chi_Minh '+caption).replace(/\s+/g,' ').trim(),'Saved cards show full year and the actual scheduling timezone');
  assert.equal(await first.locator('video').getAttribute('src'),'/api/v1/reels/assets/'+assetId+'/video','The saved schedule previews its exact local asset');
  assert.equal(await first.locator('video').getAttribute('autoplay'),null,'Saved videos never autoplay');
  await page.waitForFunction(()=>document.querySelector('article[aria-label="Lịch Reel #1"] video')?.readyState>=2);
  assert.deepEqual(await first.locator('video').evaluate(video=>[video.videoWidth,video.videoHeight]),[64,112],'Saved local previews actually decode the synthetic video');
  assert.ok(await first.locator('video').evaluate(video=>video.duration>0),'Video metadata has a genuine positive duration');
  await countIs(detail,0);
  await first.locator('video').click({position:{x:20,y:20}});
  await countIs(detail,0,'Clicking the video never opens schedule details');
  await first.locator('video').evaluate(video=>{video.pause();window.savedPreviewNode=video;video.currentTime=0.75});
  await first.click({position:{x:8,y:8}});await detail.waitFor();
  await detail.getByText('Đã đặt lịch',{exact:true}).first().waitFor();
  assert.match(await detail.innerText(),/08:45/);await detail.getByText('Asia/Ho_Chi_Minh',{exact:true}).waitFor();
  await closeDetails();
  jobs[0].message='Fixture: manual refresh keeps the existing video';
  await refreshList();await openDetails(1);await detail.getByText(jobs[0].message,{exact:true}).waitFor();await closeDetails();
  assert.equal(await first.locator('video').evaluate(video=>video===window.savedPreviewNode),true,'Refreshing preserves the existing player element');
  assert.ok(await first.locator('video').evaluate(video=>video.currentTime>=0.7),'Refresh does not reset video position');
  jobs[0].message='Fixture: polling keeps the existing video';
  await invalidateSaved();
  await openDetails(1);await detail.getByText(jobs[0].message,{exact:true}).waitFor();await closeDetails();
  assert.equal(await first.locator('video').evaluate(video=>video===window.savedPreviewNode),true,'Polling reconciles cards without replacing the player');
  assert.ok(await first.locator('video').evaluate(video=>video.currentTime>=0.7),'Polling does not reset video position');
  assert.deepEqual(semanticRequest(calls.find(call=>call.path==='/api/reels').body),{profile_id:3,page_url:'https://www.facebook.com/received-fixture',page_name:'Received Page Fixture',asset_id:assetId,caption,scheduled_local:'2030-11-05T08:45',timezone:'Asia/Ho_Chi_Minh'});
  await page.reload();await openSaved();await first.waitFor();await openDetails(1);
  assert.equal(jobs.length,1,'Saved schedules remain after reloading');
  await countIs(detail.getByRole('button',{name:'Xóa lịch Reel #1',exact:true}),0,'Scheduled jobs are not archivable');
  await detail.getByRole('button',{name:'Hủy lịch Reel #1',exact:true}).click();
  await detail.getByText('Đã hủy',{exact:true}).first().waitFor();
  assert.equal(calls.find(call=>call.path==='/api/reels/1/cancel').body,null);
  await detail.getByRole('button',{name:'Xóa lịch Reel #1',exact:true}).waitFor();
  Object.assign(jobs[0],{publish_attempted:true,can_archive:false});
  await refreshList();await openDetails(1);
  await countIs(detail.getByRole('button',{name:'Xóa lịch Reel #1',exact:true}),0,'Attempted publishing cannot be archived');
  Object.assign(jobs[0],{publish_attempted:false,can_archive:true});
  await refreshList();await openDetails(1);await detail.getByRole('button',{name:'Xóa lịch Reel #1',exact:true}).click();
  await first.waitFor({state:'hidden'});await closeDetails();
  assert.equal(calls.find(call=>call.path==='/api/reels/1/archive').body,null,'Archive is a bodyless POST');
  assert.equal(jobs.length,1);assert.ok(jobs[0].archived_at);assert.ok(assets.some(asset=>asset.id===jobs[0].asset_id));
  await page.reload();await openCreate();await form.getByRole('combobox',{name:'Video đã lưu',exact:true}).waitFor();
  await countIs(form.locator('select[aria-label="Video đã lưu"] option[value="'+assetId+'"]'),1,'Video reuse is unaffected by archive');
  await openSaved();await countIs(first,0);
  const showArchived=page.getByRole('checkbox',{name:'Hiện lịch đã xóa',exact:true});
  assert.equal(await showArchived.isChecked(),false);await showArchived.check();await first.waitFor();await openDetails(1);
  await detail.getByText('Đã xóa khỏi danh sách',{exact:true}).waitFor();
  assert.equal(listRequests.at(-1),'?include_archived=true');
  await countIs(detail.getByRole('button',{name:'Tiếp tục lịch Reel #1',exact:true}),0);
  const resumesBeforeRestore=calls.filter(call=>call.path.endsWith('/resume')).length;
  await detail.getByRole('button',{name:'Khôi phục lịch Reel #1',exact:true}).click();await detail.getByText('Đã hủy',{exact:true}).first().waitFor();
  assert.equal(calls.find(call=>call.path==='/api/reels/1/restore').body,null);
  assert.equal(jobs[0].archived_at,null);assert.equal(jobs[0].status,'cancelled');assert.equal(jobs[0].publish_attempted,false);
  assert.equal(calls.filter(call=>call.path.endsWith('/resume')).length,resumesBeforeRestore,'Restore never resumes or runs the schedule');
  await closeDetails();await showArchived.uncheck();await first.waitFor();
  const shared={profile_id:3,page_url:'https://www.facebook.com/received-fixture',page_name:'Received Page Fixture',asset_id:assetId,caption,timezone:'Asia/Ho_Chi_Minh'};
  jobs.push(makeJob(2,shared,{status:'needs_review',step:'verifying',publish_attempted:true,can_cancel:false,can_resume:true,message:'Fixture: chưa xác minh được Reel.'}),makeJob(3,shared,{status:'completed',step:'completed',publish_attempted:true,can_cancel:false,can_resume:false,published_url:'https://www.facebook.com/reel/900000000000001'}));
  await refreshList();await card(2).waitFor();await openDetails(2);
  await detail.getByText('Chưa thấy video sau khi gửi đăng. Mở link Reel để kiểm tra; hệ thống không tự đăng lại để tránh trùng Reel.',{exact:true}).waitFor();
  assert.equal(calls.filter(call=>call.path==='/api/reels/2/resume').length,0);
  for(const action of ['Hủy lịch Reel #2','Xóa lịch Reel #2'])await countIs(detail.getByRole('button',{name:action,exact:true}),0);
  await countIs(detail.getByRole('textbox',{name:'Link Reel đã đăng',exact:true}),0,'Server capability gates verification');
  await detail.getByRole('button',{name:'Tiếp tục lịch Reel #2',exact:true}).click();await detail.getByText('Đang xác minh Reel',{exact:true}).first().waitFor();
  assert.equal(calls.find(call=>call.path==='/api/reels/2/resume').body,null);
  await openDetails(1);assert.equal(await detail.getByRole('button',{name:'Xóa lịch Reel #1',exact:true}).isEnabled(),true);
  await openDetails(3);assert.equal(await detail.getByRole('link',{name:'Mở Reel đã đăng',exact:true}).getAttribute('href'),'https://www.facebook.com/reel/900000000000001');
  await countIs(detail.getByRole('button',{name:'Xóa lịch Reel #3',exact:true}),0);
  await closeDetails();await openCreate();
  await form.getByRole('combobox',{name:'Profile đăng Reel',exact:true}).selectOption('1');await form.getByRole('textbox',{name:'Link Page thủ công',exact:true}).waitFor();
  assert.equal(await form.getByRole('textbox',{name:'Link Page thủ công',exact:true}).inputValue(),'');
  await form.getByRole('textbox',{name:'Link Page thủ công',exact:true}).fill('https://invalid.example/Page');
  await form.getByRole('combobox',{name:'Video đã lưu',exact:true}).selectOption(savedId);
  await form.getByLabel('Ngày giờ đăng Reel',{exact:true}).fill('2030-11-03T01:30');await form.getByLabel('Múi giờ đăng Reel',{exact:true}).fill('America/Los_Angeles');
  await form.getByRole('textbox',{name:'Caption Reel',exact:true}).fill('Caption DST fixture');
  const beforeInvalid=calls.filter(call=>call.path==='/api/reels').length;
  assert.equal(await form.getByRole('button',{name:'Đặt lịch đăng Reel',exact:true}).isDisabled(),true);await form.getByText('Nhập link Page Facebook HTTPS hợp lệ.',{exact:true}).waitFor();
  assert.equal(calls.filter(call=>call.path==='/api/reels').length,beforeInvalid);
  await form.getByRole('textbox',{name:'Link Page thủ công',exact:true}).fill('https://www.facebook.com/unedited-fixture');
  await form.getByText('Page chưa được xác nhận đã đổi thông tin. Hoàn tất cập nhật và xác minh thông tin Page trước khi bật đăng.',{exact:true}).waitFor();
  assert.equal(await form.getByRole('button',{name:'Đặt lịch đăng Reel',exact:true}).isDisabled(),true,'Manual Page URLs obey the same information condition');
  assert.equal(calls.filter(call=>call.path==='/api/reels').length,beforeInvalid);
  await form.getByRole('textbox',{name:'Link Page thủ công',exact:true}).fill('https://www.facebook.com/manual-fixture');await form.getByRole('textbox',{name:'Tên Page thủ công',exact:true}).fill('Manual Page Fixture');
  await form.getByRole('button',{name:'Đặt lịch đăng Reel',exact:true}).click();await form.getByText('Giờ đăng bị trùng do DST. Chọn lần đầu hoặc lần thứ hai.',{exact:true}).waitFor();
  await form.getByText('Giờ trùng khi đổi giờ mùa (DST)',{exact:true}).click();await form.getByRole('combobox',{name:'Lần giờ DST',exact:true}).selectOption('1');
  await form.getByRole('button',{name:'Đặt lịch đăng Reel',exact:true}).click();await form.getByText('Đã lưu lịch. Đến giờ, Manager sẽ chạy Chrome ngầm bằng đúng profile và đăng Reel lên Page đã chọn.',{exact:true}).waitFor();
  await openSaved();const manual=card(4);await manual.waitFor();await openDetails(4);
  assert.deepEqual(semanticRequest(calls.filter(call=>call.path==='/api/reels').at(-1).body),{profile_id:1,page_url:'https://www.facebook.com/manual-fixture',page_name:'Manual Page Fixture',asset_id:savedId,caption:'Caption DST fixture',scheduled_local:'2030-11-03T01:30',timezone:'America/Los_Angeles',fold:1});
  assert.match(await detail.innerText(),/01:30/);await detail.getByText('America/Los_Angeles',{exact:true}).waitFor();await closeDetails();
  Object.assign(jobs.find(job=>job.id===2),{status:'needs_review',step:'verifying',can_verify_link:true,verification_url:'https://www.facebook.com/reel/800000000000002'});
  Object.assign(jobs.find(job=>job.id===3),{can_verify_link:true});
  jobs.push(makeJob(5,shared,{status:'needs_review',step:'verifying',publish_attempted:true,can_cancel:false,can_resume:true,can_verify_link:true,verification_url:'https://www.facebook.com/reel/800000000000005'}),makeJob(6,shared,{status:'needs_review',step:'verifying',publish_attempted:false,can_verify_link:true}));
  await refreshList();await openDetails(3);await countIs(detail.getByRole('textbox',{name:'Link Reel đã đăng',exact:true}),0);
  await openDetails(6);await countIs(detail.getByRole('textbox',{name:'Link Reel đã đăng',exact:true}),0,'Attempted latch is also required');
  await openDetails(2);let verifyInput=detail.getByRole('textbox',{name:'Link Reel đã đăng',exact:true});await verifyInput.waitFor();assert.equal(await verifyInput.inputValue(),'https://www.facebook.com/reel/800000000000002');
  const editedLink='https://m.facebook.com/reels/800000000000006/?ref=fixture',otherDraft='https://www.facebook.com/reel/800000000000007';
  await verifyInput.fill(editedLink);await openDetails(5);await detail.getByRole('textbox',{name:'Link Reel đã đăng',exact:true}).fill(otherDraft);
  await openDetails(2);verifyInput=detail.getByRole('textbox',{name:'Link Reel đã đăng',exact:true});assert.equal(await verifyInput.inputValue(),editedLink,'Verification draft survives reopening its modal');
  const listCount=listRequests.length;await invalidateSaved();assert.ok(listRequests.length>listCount);
  assert.equal(await verifyInput.inputValue(),editedLink,'Polling preserves the modal input');
  await openDetails(5);assert.equal(await detail.getByRole('textbox',{name:'Link Reel đã đăng',exact:true}).inputValue(),otherDraft,'Each modal retains an independent verification draft');
  await assertDraft('Caption DST fixture');await openDetails(2);verifyInput=detail.getByRole('textbox',{name:'Link Reel đã đăng',exact:true});
  await detail.getByText('Chỉ kiểm tra video tại link Reel đã nhập. Không đăng thêm video.',{exact:true}).waitFor();
  const postCounts=()=>({resume:calls.filter(call=>call.path.endsWith('/resume')).length,create:calls.filter(call=>call.path==='/api/reels').length,upload:calls.filter(call=>call.path==='/api/reels/assets').length}),beforeVerification=postCounts();
  for(const unsafe of ['https://facebook.com.evil.test/reel/1','https://www.facebook.com/reel/not-a-number','https://user:pass@www.facebook.com/reel/1','https://www.facebook.com:444/reel/1','http://www.facebook.com/reel/1']){
   await verifyInput.fill(unsafe);await detail.getByRole('button',{name:'Xác minh link Reel #2',exact:true}).click();await detail.getByRole('alert').getByText('Dùng link Reel Facebook HTTPS có ID số, ví dụ https://www.facebook.com/reel/123456.',{exact:true}).waitFor();
  }
  assert.equal(calls.filter(call=>call.path.endsWith('/verify-link')).length,0);
  await verifyInput.fill(editedLink);verifyMode='reject';await detail.getByRole('button',{name:'Xác minh link Reel #2',exact:true}).click();await detail.getByRole('alert').getByText('Fixture: chưa thấy video tại link Reel.',{exact:true}).waitFor();assert.equal(await verifyInput.inputValue(),editedLink);
  await openDetails(5);assert.equal(await detail.getByRole('textbox',{name:'Link Reel đã đăng',exact:true}).inputValue(),otherDraft);
  await openDetails(2);verifyInput=detail.getByRole('textbox',{name:'Link Reel đã đăng',exact:true});verifyMode='hold';await detail.getByRole('button',{name:'Xác minh link Reel #2',exact:true}).click();
  await page.waitForFunction(()=>document.querySelector('button[aria-label="Xác minh link Reel #2"]')?.disabled);assert.equal(await verifyInput.isDisabled(),true);
  await openDetails(5);assert.equal(await detail.getByRole('textbox',{name:'Link Reel đã đăng',exact:true}).isEnabled(),true,'In-flight verification disables only its job');await closeDetails();
  while(!releaseVerification)await page.waitForTimeout(20);releaseVerification();releaseVerification=null;
  await openDetails(2);await detail.getByRole('textbox',{name:'Link Reel đã đăng',exact:true}).waitFor({state:'hidden'});await detail.getByText('Đang xác minh Reel',{exact:true}).first().waitFor();
  await countIs(detail.getByRole('link',{name:'Mở Reel đã đăng',exact:true}),0,'Sending URL alone never claims publishing success');
  const verificationCalls=calls.filter(call=>call.path==='/api/reels/2/verify-link');assert.equal(verificationCalls.length,2);assert.deepEqual(verificationCalls[1].body,{reel_url:'https://www.facebook.com/reel/800000000000006'});assert.deepEqual(postCounts(),beforeVerification);
  await openDetails(5);assert.equal(await detail.getByRole('textbox',{name:'Link Reel đã đăng',exact:true}).inputValue(),otherDraft);await assertDraft('Caption DST fixture');
  Object.assign(jobs.find(job=>job.id===2),{status:'completed',step:'completed',published_url:'https://www.facebook.com/reel/800000000000006',can_cancel:false});
  await refreshList();await openDetails(2);await detail.getByRole('link',{name:'Mở Reel đã đăng',exact:true}).waitFor();assert.equal(await detail.getByRole('link',{name:'Mở Reel đã đăng',exact:true}).getAttribute('href'),'https://www.facebook.com/reel/800000000000006');await closeDetails();
  await page.evaluate(()=>window.scrollTo(0,0));
  await page.evaluate(()=>{const label=document.createElement('div');label.id='reel-fixture-label';label.textContent='FIXTURE ONLY · saved schedules · no live uploads, schedules or publishing';Object.assign(label.style,{position:'fixed',bottom:'14px',left:'20px',zIndex:99999,padding:'8px 12px',background:'#182d25',color:'#baf2d0',border:'1px solid #467d61',borderRadius:'8px',font:'13px sans-serif'});document.body.appendChild(label)});
  await page.screenshot({path:path.join(output,'reel-scheduler.png'),fullPage:true});await page.locator('#reel-fixture-label').evaluate(element=>element.remove());
  await page.getByRole('combobox',{name:'Lọc trạng thái lịch Reel',exact:true}).selectOption('completed');await card(3).waitFor();await countIs(card(4),0);
  await page.getByRole('combobox',{name:'Lọc trạng thái lịch Reel',exact:true}).selectOption('all');await page.getByRole('textbox',{name:'Tìm lịch Reel',exact:true}).fill('Manual Page Fixture');await manual.waitFor();await countIs(card(3),0);
  await page.getByRole('textbox',{name:'Tìm lịch Reel',exact:true}).fill('');
  const unsafeAssetId='c'.repeat(32),mismatchedAssetId='d'.repeat(32);
  assets.push({id:unsafeAssetId,name:'Foreign preview fixture.mp4',size:512,mime:'video/mp4',created_at:now(),preview_url:'https://foreign.invalid/video.mp4'},{id:mismatchedAssetId,name:'Mismatched preview fixture.mp4',size:512,mime:'video/mp4',created_at:now(),preview_url:'/api/reels/assets/'+savedId+'/video'});
  jobs.push(makeJob(7,{...shared,asset_id:unsafeAssetId}),makeJob(8,{...shared,asset_id:mismatchedAssetId}),makeJob(9,shared,{asset_id:'e'.repeat(32),asset_name:'Missing asset fixture.mp4'}));
  const beforePreviewNavigation=calls.length;await openCreate();await openSaved();
  for(const id of [7,8,9]){await card(id).waitFor();await countIs(card(id).locator('video'),0,'Unsafe/missing preview never shows another video for job '+id)}
  assert.equal(calls.length,beforePreviewNavigation);
  const calendarRequests=[];page.on('request',request=>{const url=new URL(request.url());if(url.pathname==='/api/v1/reels')calendarRequests.push(url)});
  jobs.push(makeJob(10,{...shared,timezone:'America/Los_Angeles'},{scheduled_at:'2027-01-01T07:30:00Z',created_at:'2020-01-01T00:00:00Z'}),
    makeJob(11,{...shared,timezone:'America/Los_Angeles'},{scheduled_at:'2027-01-01T08:30:00Z',created_at:'2020-01-02T00:00:00Z'}),
    makeJob(12,{...shared,timezone:'America/Los_Angeles'},{scheduled_at:'2027-01-02T00:30:00Z',created_at:'2020-01-03T00:00:00Z'}));
  for(let id=13;id<=23;id++)jobs.push(makeJob(id,shared,{scheduled_at:'2028-01-01T14:00:00Z'}));
  await refreshList();
  const calendarMode=page.getByRole('combobox',{name:'Lọc thời gian lịch Reel',exact:true}),calendarValue=page.getByLabel('Giá trị thời gian lịch Reel',{exact:true});
  const beforeCalendar=calls.length;
  await calendarMode.selectOption('year');await calendarValue.fill('2026');await card(10).waitFor();
  await countIs(page.locator('.reels-job-card'),1,'Global calendar filtering happens before pagination, reaching a job outside the first page');
  assert.match(await card(10).innerText(),/31\/12\/2026 23:30/);
  assert.ok(calendarRequests.some(url=>url.searchParams.get('calendar')==='2026'));
  await page.getByRole('combobox',{name:'Nhóm lịch Reel trên trang',exact:true}).selectOption('year');
  await page.getByRole('heading',{name:/^Năm 2026/}).waitFor();
  await calendarMode.selectOption('month');await calendarValue.fill('2027-01');await card(11).waitFor();await card(12).waitFor();await countIs(card(10),0);
  await page.getByRole('combobox',{name:'Thứ tự lịch Reel',exact:true}).selectOption('scheduled_asc');
  await page.waitForFunction(()=>[...document.querySelectorAll('.reels-job-card')].map(node=>node.getAttribute('aria-label')).join(',')==='Lịch Reel #11,Lịch Reel #12');
  await page.getByRole('combobox',{name:'Thứ tự lịch Reel',exact:true}).selectOption('scheduled_desc');
  await page.waitForFunction(()=>[...document.querySelectorAll('.reels-job-card')].map(node=>node.getAttribute('aria-label')).join(',')==='Lịch Reel #12,Lịch Reel #11');
  await calendarMode.selectOption('day');await calendarValue.fill('2027-01-01');await card(11).waitFor();await card(12).waitFor();
  await page.getByRole('combobox',{name:'Nhóm lịch Reel trên trang',exact:true}).selectOption('day');await page.getByRole('heading',{name:/^Ngày 01\/01\/2027/}).waitFor();
  assert.ok(calendarRequests.some(url=>url.searchParams.get('calendar')==='2027-01-01'&&url.searchParams.get('sort')==='scheduled_desc'));
  assert.equal(calls.length,beforeCalendar,'Date filters, chronological sorting and grouping never mutate schedules');
  const beforeInvalidPeriod=calendarRequests.length;
  await calendarMode.selectOption('year');await page.getByText('Chưa áp dụng bộ lọc thời gian',{exact:true}).waitFor();
  await calendarValue.fill('20');await page.waitForTimeout(100);
  await countIs(page.locator('.reels-job-card'),0,'Partial period hides results rather than implying that All is filtered');
  assert.equal(calendarRequests.length,beforeInvalidPeriod,'Invalid/partial non-All period never fetches an unfiltered list');
  jobs.push(makeJob(24,{...shared,timezone:'UTC',caption:'CONTIGUOUS CALENDAR FIXTURE'},{scheduled_at:'2027-01-01T06:00:00Z'}),
    makeJob(25,{...shared,timezone:'America/Los_Angeles',caption:'CONTIGUOUS CALENDAR FIXTURE'},{scheduled_at:'2027-01-01T07:00:00Z'}),
    makeJob(26,{...shared,timezone:'UTC',caption:'CONTIGUOUS CALENDAR FIXTURE'},{scheduled_at:'2027-01-01T08:00:00Z'}));
  await calendarMode.selectOption('all');await page.getByLabel('Tìm lịch Reel',{exact:true}).fill('CONTIGUOUS CALENDAR FIXTURE');
  await page.getByLabel('Thứ tự lịch Reel',{exact:true}).selectOption('scheduled_asc');
  await page.waitForFunction(()=>[...document.querySelectorAll('.reels-job-card')].map(node=>node.getAttribute('aria-label')).join(',')==='Lịch Reel #24,Lịch Reel #25,Lịch Reel #26');
  await countIs(page.locator('.reels-calendar-heading'),3,'Noncontiguous same-day groups repeat without undoing UTC order');
  await page.getByLabel('Thứ tự lịch Reel',{exact:true}).selectOption('scheduled_desc');
  await page.waitForFunction(()=>[...document.querySelectorAll('.reels-job-card')].map(node=>node.getAttribute('aria-label')).join(',')==='Lịch Reel #26,Lịch Reel #25,Lịch Reel #24');
  for(const width of [960,600,390]){await page.setViewportSize({width,height:900});await page.waitForFunction(()=>document.documentElement.scrollWidth===innerWidth);assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,'No page overflow at '+width)}
  assert.equal(calls.filter(call=>/facebook\/check|page-recipient|outlook/.test(call.path)).length,0,'No deferred scanner or Outlook workflow is invoked');
  assert.ok(mediaRequests.some(request=>request.path==='/api/v1/reels/assets'&&request.method==='POST'),'Binary XHR uploads use the versioned resource endpoint');
  assert.ok(mediaRequests.some(request=>request.path==='/api/v1/reels/assets/'+assetId+'/video'&&request.method==='GET'),'Legacy stored preview URLs fetch the canonical versioned media endpoint');
  assert.equal(mediaRequests.some(request=>!request.path.startsWith('/api/v1/')),false,'No upload or playback request bypasses API v1');
  assert.deepEqual(errors,[]);assert.deepEqual(unexpected,[]);
  console.log('PASS: compact Page/caption/video cards with click-to-open details, video clicks isolated, exact local MP4 decode and playback continuity; preserved create/verification drafts, guarded modal schedule actions, filters/responsive UI. Fixtures only. Screenshot: .runtime/ui/reel-scheduler.png');
 }finally{await browser.close()}
})().catch(error=>{console.error(error);process.exit(1)});
