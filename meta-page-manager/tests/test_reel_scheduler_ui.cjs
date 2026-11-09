// Offline synthetic Page Manager only. No real token, upload, schedule or publication.
const {chromium}=require('../../chrome-manager/frontend/node_modules/playwright');
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const root=path.resolve(__dirname,'..'),frontend=path.join(root,'frontend'),output=path.join(root,'.runtime/ui'),base=process.argv.includes('--mounted')?'/pages':'',origin=base?'http://127.0.0.1:8000':'http://127.0.0.1:8010';
const video=fs.readFileSync(path.join(__dirname,'fixtures','saved-schedule-preview.mp4')),stamp=new Date().toISOString();
(async()=>{
 const browser=await chromium.launch({channel:'chrome',headless:true});
 try{
  const context=await browser.newContext({offline:true,viewport:{width:1500,height:1050}}),page=await context.newPage(),errors=[],outside=[],writes=[],reads=[];
  let session=0,enabled=true,configMissing=false,createMode='normal',delayJobs=false,jobsRelease=null,nextId=10;
  const csrf=()=>`reel-fixture-${session}`,shot=name=>path.join(output,(base?'mounted-':'')+name);
  const edge=(id,label)=>({id,label,status:'working',sources:['me/accounts'],tasks:['CREATE_CONTENT'],has_page_token:true});
  const pages=[{id:'90000001',name:'Page A · Token',can_publish:true,information_status:'updated',status:'active',category:'Fixture',connections:[edge(1,'Nhật · Nguồn A')],details:{},posts_today:null,posts_today_complete:false},{id:'90000002',name:'Page B · Token',can_publish:true,information_status:'updated',status:'active',category:'Fixture',connections:[edge(2,'Nguồn B')],details:{},posts_today:null,posts_today_complete:false},{id:'90000003',name:'Page chưa đổi thông tin',can_publish:false,information_status:'not_updated',status:'active',connections:[edge(1,'Nhật · Nguồn A')],details:{},posts_today:null,posts_today_complete:false}];
  const assets=[{id:'a'.repeat(32),name:'Video đã lưu.mp4',size:video.length,mime:'video/mp4',created_at:stamp,preview_url:'/api/reels/assets/'+'a'.repeat(32)+'/video'},
   {id:'c'.repeat(32),name:'URL ngoài.mp4',size:video.length,mime:'video/mp4',preview_url:'https://outside.invalid/private-video.mp4'},
   {id:'d'.repeat(32),name:'Sai asset.mp4',size:video.length,mime:'video/mp4',preview_url:'/api/reels/assets/'+'a'.repeat(32)+'/video'}];
  const job=(id,status,extra={})=>({id,request_id:`00000000-0000-4000-8000-${String(id).padStart(12,'0')}`,page_id:'90000001',page_name:pages[0].name,connection_id:1,connection_label:'Nhật · Nguồn A',asset_id:assets[0].id,asset_name:assets[0].name,caption:'Caption fixture '+id,scheduled_at:'2030-11-01T10:00:00Z',scheduled_local:'2030-11-01T03:00',timezone:'America/Los_Angeles',fold:0,status,step:status,message:'Thông báo an toàn fixture.',video_id:null,published_url:null,created_at:stamp,updated_at:stamp,started_at:null,finished_at:null,can_cancel:false,can_resume:false,can_verify:false,archived_at:null,...extra});
  let jobs=[job(1,'completed',{published_url:'https://www.facebook.com/reel/1111111111111'}),job(2,'needs_review',{can_verify:true}),job(3,'needs_user',{can_resume:true}),job(4,'scheduled',{can_cancel:true,publish_mode:'on_approval',approve_by:'2030-11-02T14:00:00Z'}),
   job(5,'scheduled',{asset_id:'f'.repeat(32),asset_name:'Video không còn trong kho.mp4'}),job(6,'scheduled',{asset_id:'c'.repeat(32),asset_name:'URL ngoài.mp4'}),job(7,'scheduled',{asset_id:'d'.repeat(32),asset_name:'Sai asset.mp4'})];
  await page.addInitScript(()=>{const OriginalDate=Date;window.fixtureNow='2030-11-02T06:30:00Z';window.Date=class extends OriginalDate{constructor(...args){super(...(args.length?args:[window.fixtureNow]))}static now(){return new OriginalDate(window.fixtureNow).getTime()}}});
  await page.addInitScript(()=>{const original=window.setInterval;window.setInterval=(callback,delay,...args)=>{if(delay===15000){window.fixtureReelPoll=callback;return original(()=>{},delay)}return original(callback,delay,...args)}});
  await page.route('**/*',async route=>{
   const request=route.request(),url=new URL(request.url());
   if(url.origin===origin&&url.pathname==='/auth/api/me')return route.fulfill({json:{user:{id:1,username:'fixture',role:'admin',must_change_password:false},csrf_token:'fixture-via-csrf'}});
   if(url.origin!==origin||!url.pathname.startsWith(base+'/')){outside.push(url.pathname);return route.abort()}
   const p=url.pathname.slice(base.length),method=request.method(),upload=p==='/api/reels/assets'&&method==='POST';
   if(p.startsWith('/api/')){
    let body=request.postData()?upload?request.postDataBuffer():request.postDataJSON():undefined;
    if(method==='GET')reads.push(p);else{assert.equal(request.headers()['x-mpm-csrf'],csrf());assert.equal(request.headers()['x-via-csrf'],'fixture-via-csrf');writes.push({path:p,method,body:upload?null:body,filename:request.headers()['x-filename']})}
    if(p==='/api/session'){session++;return route.fulfill({json:{csrf_token:csrf(),app:{name:'Page Manager',version:'fixture',timezone:'UTC',token_reels:enabled,page_editing:true}}})}
    if(p==='/api/overview')return route.fulfill({json:{stats:{pages:2,connections:2,working_connections:2,needs_attention:0,pages_with_posts_today:0,today_count_known:false},updated_at:stamp}});
    if(p==='/api/pages')return route.fulfill({json:{items:pages}});
    if(p==='/api/connections')return route.fulfill({json:{items:pages.map((page,index)=>({...page.connections[0],connected:true,has_token:true,token_type:'user',page_count:1,id:index+1}))}});
    if(p==='/api/events')return route.fulfill({json:{items:[]}});
    if(p==='/api/reels/config')return configMissing?route.fulfill({status:404,json:{detail:'Feature unavailable in fixture'}}):route.fulfill({json:{scheduler_running:true,active_count:0,max_video_bytes:1073741824,video_extensions:['.mp4','.m4v','.mov'],default_timezone:'America/Los_Angeles',timezones:['America/Los_Angeles','Asia/Ho_Chi_Minh','UTC'],max_parallel:2}});
    if(p==='/api/reels/assets'&&method==='GET')return route.fulfill({json:{assets}});
    if(upload){assert.equal(request.headers()['content-type'],'application/octet-stream');assert.deepEqual(body,video);const asset={...assets[0],id:'b'.repeat(32),name:decodeURIComponent(request.headers()['x-filename']),preview_url:'/api/reels/assets/'+'b'.repeat(32)+'/video'};assets.push(asset);return route.fulfill({json:{asset}})}
    if(/^\/api\/reels\/assets\/[a-z0-9]+\/video$/.test(p)){
     const range=/^bytes=(\d+)-(\d*)$/.exec(request.headers().range||'');
     if(range){const start=Number(range[1]),end=Math.min(range[2]?Number(range[2]):video.length-1,video.length-1);return route.fulfill({status:206,body:video.subarray(start,end+1),contentType:'video/mp4',headers:{'accept-ranges':'bytes','content-range':`bytes ${start}-${end}/${video.length}`}})}
     return route.fulfill({body:video,contentType:'video/mp4',headers:{'accept-ranges':'bytes'}});
    }
    if(p==='/api/reels'&&method==='GET'){
     const value=JSON.parse(JSON.stringify({jobs,scheduler:{scheduler_running:true,active_count:0}}));
     if(delayJobs){delayJobs=false;await new Promise(resolve=>{jobsRelease=resolve})}
     return route.fulfill({json:value}).catch(()=>{});
    }
    if(p==='/api/reels'&&method==='POST'){
     assert.match(body.request_id,/^[a-f0-9-]{36}$/);assert.equal(body.page_id,'90000001');assert.equal(body.connection_id,1);
     if(createMode==='422-once'){createMode='normal';return route.fulfill({status:422,json:{detail:'Giờ đăng không hợp lệ trong fixture'}})}
     if(createMode==='503-once'){createMode='normal';return route.fulfill({status:503,json:{detail:'Temporary fixture failure'}})}
     if(createMode==='information-once'){createMode='normal';pages[0].can_publish=false;return route.fulfill({status:422,json:{detail:'Page chưa đổi thông tin; chưa được bật đăng.',code:'page_information_required'}})}
     let stored=jobs.find(value=>value.request_id===body.request_id),replayed=!!stored;
     if(!stored){stored=job(nextId++,'scheduled',{...body,page_name:pages[0].name,connection_label:'Nhật · Nguồn A',asset_name:assets.find(asset=>asset.id===body.asset_id)?.name,can_cancel:true});jobs.push(stored)}
     if(createMode==='network-once'){createMode='normal';return route.abort('failed')}
     return route.fulfill({json:{job:stored,replayed}});
    }
    if(/^\/api\/reels\/\d+\/(cancel|resume|verify)$/.test(p)){
     assert.deepEqual(body,{});const id=Number(p.split('/')[3]),action=p.split('/')[4],item=jobs.find(value=>value.id===id);assert.equal(item['can_'+action],true);
     Object.assign(item,{status:action==='cancel'?'cancelled':action==='resume'?'scheduled':'completed',step:'fixture_'+action,can_cancel:action==='resume',can_resume:false,can_verify:false,updated_at:new Date().toISOString(),...(action==='verify'?{published_url:'https://www.facebook.com/reel/2222222222222'}:{})});
     return route.fulfill({json:{job:item}});
    }
    return route.fulfill({status:404,json:{detail:'Unknown fixture API'}});
   }
   if(['/','/schedule','/saved','/settings'].includes(p))return route.fulfill({path:path.join(frontend,'index.html'),contentType:'text/html'});
   if(p.startsWith('/static/')){const file=path.resolve(frontend,p.slice(8));if(!file.startsWith(frontend+path.sep)||!fs.existsSync(file))return route.abort();return route.fulfill({path:file,contentType:file.endsWith('.js')?'application/javascript':file.endsWith('.woff2')?'font/woff2':'text/css'})}
   return route.abort();
  });
  page.on('pageerror',error=>errors.push(error.message));await page.goto(origin+base+'/');await page.getByRole('heading',{name:'Pages',exact:true}).waitFor();
  assert.equal(reads.some(value=>value.startsWith('/api/reels')),false,'Pages startup does not load scheduler endpoints');
  await page.getByRole('button',{name:'Lịch đăng Page',exact:true}).click();await page.getByRole('combobox',{name:'Page đăng Reel',exact:true}).waitFor();
  assert.equal(writes.length,0,'Opening scheduler does not upload, create or publish');
  assert.equal(await page.getByRole('article',{name:/Lịch Reel token #/}).count(),0,'Create view no longer includes saved schedules');
  const savedNavigation=()=>page.getByRole('navigation',{name:'Điều hướng chính',exact:true}).getByRole('button',{name:'Lịch đã lưu',exact:true});
  const composeNavigation=()=>page.getByRole('navigation',{name:'Điều hướng chính',exact:true}).getByRole('button',{name:'Lịch đăng Page',exact:true});
  const pageSelect=page.getByRole('combobox',{name:'Page đăng Reel',exact:true}),source=page.getByRole('combobox',{name:'Nguồn token đăng Reel',exact:true});
  assert.equal(await pageSelect.locator('option[value="90000003"]').count(),0,'Unedited Page is hidden from schedule picker');
  assert.equal(await page.getByRole('button',{name:'Chỉnh sửa Page chưa đổi thông tin',exact:true}).count(),0,'No ineligible edit-link list appears in create scheduler');
  await pageSelect.selectOption('90000001');await source.selectOption('1');await pageSelect.selectOption('90000002');assert.equal(await source.inputValue(),'','Page change clears source');assert.equal(await source.getByRole('option',{name:/Nhật/}).count(),0,'Source from another Page is never offered');
  await pageSelect.selectOption('90000001');await source.selectOption('1');
  await page.getByLabel('Chọn video Reel từ máy',{exact:true}).setInputFiles({name:'Thử video.mp4',mimeType:'video/mp4',buffer:video});assert.equal(writes.length,0,'Selecting video creates only a local preview');
  await page.getByRole('button',{name:'Tải video lên manager',exact:true}).click();await page.getByText('Video đã lưu trên manager. Chưa tạo lịch và chưa gửi lên Meta.',{exact:true}).waitFor();
  assert.equal(writes.length,1);assert.equal(writes[0].path,'/api/reels/assets');assert.equal(writes[0].filename,encodeURIComponent('Thử video.mp4'));
  assert.equal(await page.getByRole('combobox',{name:'Video đã lưu trên manager',exact:true}).inputValue(),'b'.repeat(32));assert.equal(await page.locator('video.token-reels-preview').getAttribute('src'),base+'/api/reels/assets/'+'b'.repeat(32)+'/video','Preview belongs to Page Manager namespace');
  async function stage(caption){await page.getByRole('textbox',{name:'Caption Reel',exact:true}).fill(caption);await page.getByLabel('Thời gian đăng Reel',{exact:true}).fill('2030-11-01T03:00');await page.getByRole('combobox',{name:'Múi giờ lịch Reel',exact:true}).selectOption('America/Los_Angeles');await page.getByRole('button',{name:'Xem lại lịch',exact:true}).click();await page.getByRole('heading',{name:'Đăng công khai lên Page đã chọn',exact:true}).waitFor()}
  await stage('Caption chỉ có trong fixture <img src=x onerror="window.bad=true">');assert.equal(writes.filter(value=>value.path==='/api/reels').length,0,'Review creates no schedule');
  await page.locator('.token-reels-review').getByText('01/11/2030 03:00',{exact:true}).waitFor();
  await savedNavigation().click();await composeNavigation().click();await page.getByRole('heading',{name:'Đăng công khai lên Page đã chọn',exact:true}).waitFor();assert.equal(writes.filter(value=>value.path==='/api/reels').length,0,'Navigating away from a review and back never creates a schedule');
  await page.getByText('Nhật · Nguồn A · #1',{exact:true}).waitFor();fs.mkdirSync(output,{recursive:true});await page.evaluate(()=>scrollTo(0,0));await page.screenshot({path:shot('token-reels-review.png'),fullPage:true});
  await page.getByRole('button',{name:'Đặt lịch Reel bằng token',exact:true}).evaluate(button=>{button.click();button.click()});await page.getByText(/^Đã ghi nhận lịch #10/).waitFor();
  const firstCreate=writes.filter(value=>value.path==='/api/reels');assert.equal(firstCreate.length,1,'Double-click creates one metadata request');assert.equal(firstCreate[0].body.asset_id,'b'.repeat(32));assert.equal(firstCreate[0].body.timezone,'America/Los_Angeles');assert.equal(firstCreate[0].body.scheduled_local,'2030-11-01T03:00');assert.equal(await page.evaluate(()=>window.bad||false),false);
  await page.getByRole('textbox',{name:'Caption Reel',exact:true}).fill('Bản nháp cần giữ khi cập nhật hàng đợi');await page.evaluate(()=>window.fixtureReelPoll());await page.waitForTimeout(80);assert.equal(await page.getByRole('textbox',{name:'Caption Reel',exact:true}).inputValue(),'Bản nháp cần giữ khi cập nhật hàng đợi');
  const writesBeforeSaved=writes.length;await savedNavigation().click();await page.getByRole('heading',{name:'Lịch đã lưu',exact:true}).waitFor();
  const immediate=page.getByRole('article',{name:'Lịch Reel token #4',exact:true});
  await immediate.getByText('Đăng ngay sau khi bạn duyệt',{exact:true}).waitFor();
  assert.equal(await immediate.locator('time').count(),0,'Immediate request anchor is never displayed as a scheduled appointment');
  assert.equal(await immediate.getByText(/^Hạn duyệt:/).count(),1,'Immediate review cutoff stays visible');
  const oldImmediate=jobs.find(value=>value.id===4);
  jobs=jobs.map(value=>value.id===4?{...value,approval_policy:'remind_until_decision_v1',approve_by:null,publication_approval:{approval_policy:'remind_until_decision_v1',reminder_interval_seconds:300,state:'pending',can_publish:false}}:value);
  await page.getByRole('button',{name:'Làm mới lịch',exact:true}).click();
  await immediate.getByText('Chờ bạn quyết định · nhắc mỗi 5 phút sau giờ đăng',{exact:true}).waitFor();
  assert.equal(await immediate.getByText(/^Hạn duyệt:/).count(),0,'Continuous approval never displays a missing deadline');
  jobs=jobs.map(value=>value.id===4?oldImmediate:value);await page.getByRole('button',{name:'Làm mới lịch',exact:true}).click();
  await immediate.getByText(/^Hạn duyệt:/).waitFor();
  assert.equal(await page.getByRole('combobox',{name:'Page đăng Reel',exact:true}).count(),0,'Saved view does not duplicate schedule form');
  assert.equal(writes.length,writesBeforeSaved,'Opening saved schedules never changes the queue');
  const detail=id=>page.getByRole('dialog',{name:'Chi tiết lịch Reel #'+id,exact:true}),openDetail=async id=>{await page.getByRole('button',{name:'Xem chi tiết lịch Reel token #'+id,exact:true}).click();await detail(id).waitFor()},closeDetail=async()=>{await page.getByRole('button',{name:'Đóng chi tiết lịch Reel',exact:true}).click()};
  const completed=page.getByRole('article',{name:'Lịch Reel token #1',exact:true});
  assert.equal(await completed.getByRole('button').count(),1,'Compact card only has a Page-name detail trigger');assert.equal(await completed.getByRole('link').count(),0,'Published link stays inside details');assert.equal(await completed.getByText('Thông báo an toàn fixture.',{exact:true}).count(),0,'Status/message stays inside details');assert.equal(await completed.getByText(assets[0].name,{exact:true}).count(),0,'Filename stays inside details');
  await openDetail(1);assert.equal(await detail(1).getByRole('link',{name:'Mở Reel đã đăng',exact:true}).getAttribute('href'),'https://www.facebook.com/reel/1111111111111');await closeDetail();
  const preview=completed.locator('video.token-reels-job-preview');await preview.waitFor();
  assert.equal(await preview.getAttribute('src'),base+'/api/reels/assets/'+'a'.repeat(32)+'/video');
  assert.equal(await preview.getAttribute('autoplay'),null);assert.equal(await preview.getAttribute('controls'),'true');
  await page.waitForFunction(()=>document.querySelector('video.token-reels-job-preview')?.readyState>=2);
  assert.equal(await preview.evaluate(node=>node.videoWidth),64,'A real synthetic MP4 decodes in the saved schedule preview');
  // Native controls may queue playback until the decoder is ready. Settle that
  // actual user action before establishing the paused-position baseline.
  await preview.evaluate(node=>{window.fixtureNativePlaying=new Promise(resolve=>node.addEventListener('playing',()=>resolve(true),{once:true}))});
  await preview.click();assert.equal(await page.getByRole('dialog').count(),0,'Using video controls does not open schedule details');await page.evaluate(()=>Promise.race([window.fixtureNativePlaying,new Promise((_,reject)=>setTimeout(()=>reject(Error('Native playback did not start')),3000))]));await preview.evaluate(node=>new Promise(resolve=>{if(node.paused)return resolve(true);node.addEventListener('pause',()=>resolve(true),{once:true});node.pause()}));
  const beforePollTime=await preview.evaluate(async node=>{node.pause();node.currentTime=0.7;window.fixtureSavedVideo=node;if(node.seeking)await new Promise(resolve=>node.addEventListener('seeked',resolve,{once:true}));return node.currentTime});assert.ok(Math.abs(beforePollTime-0.7)<0.1,'Fixture video supports a real seek before refreshing');
  await openDetail(1);jobs[0].message='Đã đọc trạng thái mới, giữ nguyên video';await page.evaluate(()=>window.fixtureReelPoll());await detail(1).getByText(jobs[0].message,{exact:true}).waitFor();await closeDetail();
  assert.equal(await preview.evaluate(node=>node===window.fixtureSavedVideo),true,'Polling reuses the video DOM node');const afterPollTime=await preview.evaluate(node=>node.currentTime);assert.ok(Math.abs(afterPollTime-0.7)<0.1,'Polling retains playback position: '+afterPollTime);
  for(const id of [5,6,7])assert.equal(await page.getByRole('article',{name:'Lịch Reel token #'+id,exact:true}).locator('video').count(),0,'Missing, foreign and mismatched assets never become preview sources');
  await page.getByRole('searchbox',{name:'Tìm lịch Reel token',exact:true}).fill('Video không còn trong kho');assert.equal(await page.getByRole('article',{name:/Lịch Reel token #/}).count(),1);
  await page.getByRole('searchbox',{name:'Tìm lịch Reel token',exact:true}).fill('');await page.getByRole('combobox',{name:'Lọc lịch Reel token',exact:true}).selectOption('completed');assert.equal(await page.getByRole('article',{name:/Lịch Reel token #/}).count(),1);await page.getByRole('combobox',{name:'Lọc lịch Reel token',exact:true}).selectOption('all');
  await page.getByRole('article',{name:'Lịch Reel token #2',exact:true}).locator('video').evaluate(node=>node.dispatchEvent(new Event('error')));
  await page.getByRole('article',{name:'Lịch Reel token #2',exact:true}).getByText(/không.*(?:tải|đọc|phát)/i).waitFor();await openDetail(2);assert.equal(await detail(2).getByRole('button',{name:'Xác minh trạng thái Reel token #2',exact:true}).isEnabled(),true,'Media failure does not block queue actions');await closeDetail();
  await page.evaluate(()=>scrollTo(0,0));await page.screenshot({path:shot('token-saved-schedules.png'),fullPage:true});
  await openDetail(2);await detail(2).getByRole('button',{name:'Xác minh trạng thái Reel token #2',exact:true}).click();await detail(2).getByRole('link',{name:'Mở Reel đã đăng',exact:true}).waitFor();await closeDetail();
  await openDetail(3);await detail(3).getByRole('button',{name:'Tiếp tục Reel token #3',exact:true}).click();await detail(3).getByRole('button',{name:'Hủy lịch Reel token #3',exact:true}).waitFor();await closeDetail();
  delayJobs=true;await page.getByRole('button',{name:'Làm mới lịch',exact:true}).click();for(let i=0;i<50&&!jobsRelease;i++)await page.waitForTimeout(20);assert.ok(jobsRelease);
  await openDetail(4);await detail(4).getByRole('button',{name:'Hủy lịch Reel token #4',exact:true}).click();await detail(4).locator('.token-reels-detail-status').getByText('Đã hủy',{exact:true}).waitFor();jobsRelease();jobsRelease=null;await page.waitForTimeout(80);assert.equal(await detail(4).getByRole('button',{name:'Hủy lịch Reel token #4',exact:true}).count(),0,'Stale queue GET cannot restore cancelled action');await closeDetail();
  await composeNavigation().click();assert.equal(await page.getByRole('textbox',{name:'Caption Reel',exact:true}).inputValue(),'Bản nháp cần giữ khi cập nhật hàng đợi','Switching between saved and compose retains the draft');assert.equal(await source.inputValue(),'1');assert.equal(await page.getByRole('combobox',{name:'Video đã lưu trên manager',exact:true}).inputValue(),'b'.repeat(32));
  createMode='503-once';await stage('Yêu cầu cần thử xác nhận cùng UUID');const beforeRetry=writes.filter(value=>value.path==='/api/reels').length;await page.getByRole('button',{name:'Đặt lịch Reel bằng token',exact:true}).click();await page.getByRole('button',{name:'Thử xác nhận cùng yêu cầu lịch Reel',exact:true}).waitFor();await page.waitForTimeout(80);assert.equal(writes.filter(value=>value.path==='/api/reels').length,beforeRetry+1,'Metadata POST is not retried automatically');
  await page.getByRole('button',{name:'Thử xác nhận cùng yêu cầu lịch Reel',exact:true}).click();await page.getByText(/^Đã ghi nhận lịch #11/).waitFor();const retries=writes.filter(value=>value.path==='/api/reels').slice(-2);assert.deepEqual(retries[0].body,retries[1].body,'Explicit retry preserves exact metadata and creation UUID');
  createMode='network-once';await stage('Kết quả ngắt mạng được tìm bằng request_id');await page.getByRole('button',{name:'Đặt lịch Reel bằng token',exact:true}).click();await page.getByRole('button',{name:'Kiểm tra danh sách lịch',exact:true}).waitFor();const interruptedCount=writes.filter(value=>value.path==='/api/reels').length;await page.getByRole('button',{name:'Kiểm tra danh sách lịch',exact:true}).click();await page.getByText(/^Đã ghi nhận lịch #12/).waitFor();assert.equal(writes.filter(value=>value.path==='/api/reels').length,interruptedCount,'Interrupted create is recovered by GET, never reposted');
  createMode='422-once';await stage('Lịch bị từ chối để chỉnh lại giờ');await page.getByRole('button',{name:'Đặt lịch Reel bằng token',exact:true}).click();await page.getByText(/Lịch chưa được tạo; quay lại chỉnh sửa trước khi xác nhận/).waitFor();const rejected=writes.filter(value=>value.path==='/api/reels').at(-1).body;
  await page.getByRole('button',{name:'Quay lại chỉnh sửa',exact:true}).click();await page.getByLabel('Thời gian đăng Reel',{exact:true}).fill('2030-11-01T04:00');await page.getByRole('button',{name:'Xem lại lịch',exact:true}).click();await page.getByRole('button',{name:'Đặt lịch Reel bằng token',exact:true}).click();await page.getByText(/^Đã ghi nhận lịch #13/).waitFor();const corrected=writes.filter(value=>value.path==='/api/reels').at(-1).body;assert.notEqual(corrected.request_id,rejected.request_id,'Definite rejection allows an edited new review');assert.equal(corrected.scheduled_local,'2030-11-01T04:00');
  createMode='network-once';await stage('Mạng ngắt giữ nguyên UUID khi người dùng thử lại');await page.getByRole('button',{name:'Đặt lịch Reel bằng token',exact:true}).click();await page.getByRole('button',{name:'Thử xác nhận cùng yêu cầu lịch Reel',exact:true}).waitFor();await page.getByRole('button',{name:'Thử xác nhận cùng yêu cầu lịch Reel',exact:true}).click();await page.getByText(/^Đã ghi nhận lịch #14/).waitFor();const networkRetries=writes.filter(value=>value.path==='/api/reels').slice(-2);assert.deepEqual(networkRetries[0].body,networkRetries[1].body,'Network uncertainty keeps exact creation UUID and metadata');assert.equal(jobs.filter(value=>value.request_id===networkRetries[0].body.request_id).length,1,'Explicit uncertain retry never duplicates the durable schedule');
  createMode='information-once';await stage('Policy rejection is definitive');await page.getByRole('button',{name:'Đặt lịch Reel bằng token',exact:true}).click();
  await page.getByText(/Lịch chưa được tạo; chỉnh sửa Page trước khi chọn lại/).waitFor();
  assert.equal(await page.getByRole('button',{name:'Thử xác nhận cùng yêu cầu lịch Reel',exact:true}).count(),0,'Eligibility rejection is not an uncertain submission');
  await page.waitForFunction(()=>!document.querySelector('select[aria-label="Page đăng Reel"] option[value="90000001"]'));
  assert.equal(await pageSelect.locator('option[value="90000001"]').count(),0,'Fresh blocked policy hides a stale selected Page');assert.equal(await pageSelect.inputValue(),'');assert.equal(await source.inputValue(),'');
  pages[0].can_publish=true;await page.getByRole('searchbox',{name:'Tìm Page đăng Reel',exact:true}).fill('Page A');await page.waitForTimeout(300);await pageSelect.selectOption('90000001');await source.selectOption('1');
  await page.getByRole('textbox',{name:'Caption Reel',exact:true}).fill('Bản nháp khi chuyển trang');await page.getByRole('button',{name:'Pages',exact:true}).click();await page.getByRole('button',{name:'Lịch đăng Page',exact:true}).click();assert.equal(await page.getByRole('textbox',{name:'Caption Reel',exact:true}).inputValue(),'Bản nháp khi chuyển trang','Navigation retains in-memory draft');
  await page.setViewportSize({width:390,height:844});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,'Mobile token scheduler has no page overflow');await page.evaluate(()=>scrollTo(0,0));await page.screenshot({path:shot('token-reels-mobile.png'),fullPage:true});
  jobs.push(...Array.from({length:14},(_,index)=>job(20+index,'scheduled')));const beforePaging=writes.length;await savedNavigation().click();await page.getByRole('button',{name:'Làm mới lịch',exact:true}).click();await page.getByRole('button',{name:'Trang lịch sau',exact:true}).waitFor({state:'visible'});await page.waitForFunction(()=>document.querySelectorAll('.token-reels-job').length===12);
  assert.equal(await page.locator('video.token-reels-job-preview').count()<=12,true,'Saved video previews are bounded by pagination');await page.getByRole('button',{name:'Trang lịch sau',exact:true}).click();await page.getByRole('button',{name:'Trang lịch trước',exact:true}).waitFor({state:'visible'});assert.equal(await page.getByRole('button',{name:'Trang lịch trước',exact:true}).isEnabled(),true);assert.equal(writes.length,beforePaging,'Filtering, pagination and refreshing are reads only');
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,'Mobile saved schedules have no page overflow');await page.evaluate(()=>scrollTo(0,0));await page.screenshot({path:shot('token-saved-schedules-mobile.png'),fullPage:true});
  assert.equal(await page.evaluate(()=>localStorage.length),0);assert.equal(await page.evaluate(()=>sessionStorage.length),0);assert.equal(writes.some(value=>/profiles|facebook\/check|page-recipient/.test(value.path)),false,'Token scheduler never calls Chrome actions');
  enabled=false;const oldReads=reads.filter(value=>value.startsWith('/api/reels')).length;await page.reload();await page.getByRole('button',{name:'Lịch đăng Page',exact:true}).click();await page.getByRole('heading',{name:'Chưa có tính năng lên lịch bằng token',exact:true}).waitFor();assert.equal(reads.filter(value=>value.startsWith('/api/reels')).length,oldReads,'Old session capability never calls scheduler routes');
  enabled=true;configMissing=true;await page.reload();await page.getByRole('button',{name:'Lịch đăng Page',exact:true}).click();await page.getByRole('button',{name:'Đọc lại cấu hình',exact:true}).waitFor();assert.equal(await page.getByRole('button',{name:'Xem lại lịch',exact:true}).count(),0,'Unavailable config does not claim working scheduling');
  assert.deepEqual(errors,[]);assert.deepEqual(outside,[]);
  console.log('PASS: '+(base?'mounted':'standalone')+' offline token Reel scheduler: exact Page/source, local then manager preview, explicit review/public scheduling, UUID double-click/retry/recovery, flag-gated queue actions, stale GET guard, draft preservation, capability/config fallback, responsive namespace and no browser storage/Chrome API calls. Synthetic requests only.');
 }finally{await browser.close()}
})().catch(error=>{console.error(error);process.exit(1)});
