// Offline browser checks for navigation, REST v1 and view-scoped request counts.
const {chromium}=require('../../chrome-manager/frontend/node_modules/playwright');
const path=require('node:path'),assert=require('node:assert/strict');
const frontend=path.resolve(__dirname,'../frontend'),base=process.argv.includes('--mounted')?'/pages':'',origin=base?'http://127.0.0.1:8000':'http://127.0.0.1:8010';

(async()=>{
 const browser=await chromium.launch({channel:'chrome',headless:true});
 try{
  const context=await browser.newContext({offline:true}),page=await context.newPage();
  const requests=[],calls=[],media=[],writes=[],unexpected=[],errors=[],releases=[];
  let holdRefresh=false,held,identityReads=0,sessionReads=0,forbiddenRead='';
  const waitHeld=new Promise(resolve=>{held=resolve});
  const source={id:1,label:'Fixture token',token_type:'page',status:'working',connected:true,has_token:true,page_count:1};
  const pageFixture={id:'555',name:'Fixture page',status:'active',can_publish:true,information_status:'updated',connections:[source],avatar_url:'/api/pages/555/avatar'};
  const config={max_video_bytes:1_000_000,video_extensions:['.mp4'],scheduler_running:true,default_timezone:'UTC',timezones:['UTC']};
  const ideaId='11111111-1111-4111-8111-111111111111',assetId='a'.repeat(32),mixId='b'.repeat(64),approvalId='22222222-2222-4222-8222-222222222222';
  const idea={id:ideaId,filename:'Fixture idea.png',status:'new',created_at:new Date().toISOString(),variants:[]};
  const libraryAsset={id:assetId,name:'Fixture video.mp4',size:100,mime:'video/mp4',created_at:new Date().toISOString(),preview_url:'/api/reels/assets/'+assetId+'/video',linked_jobs:[]};
  const automation={settings:{enabled:false,revision:1},active_batch:null,preview:{allocations:[],capacity:14},blockers:[]};
  const telegram={enabled:false,configured:false,revision:1,check:{ok:false},pending:{items:[{request_id:approvalId,binding:{page_id:'555'},status:'pending'}],total:1}};
  const audio={render_signature:'c'.repeat(64),items:[{id:mixId,source_filename:'Fixture song.mp3',prepared:true,pcm_sha256:'d'.repeat(64),status:'pending',preview_url:'/api/idea-pipeline/audio-mixes/'+mixId+'/audio'}]};
  const producers={providers:{codex:{status:'not_checked'},claude:{status:'not_checked'}}};
  let holdPipeline=false,pipelineHeld,releasePipeline,runs=[];
  const count=route=>requests.filter(value=>value===route).length;
  const nav=name=>page.getByRole('button',{name,exact:true});
  const waitResponse=route=>page.waitForResponse(response=>new URL(response.url()).pathname===base+route);
  page.setDefaultTimeout(8000);
  await page.addInitScript(()=>{const original=window.setInterval;window.setInterval=(call,delay,...args)=>{if(delay===10000){window.fixtureIdeaPoll=call;return original(()=>{},delay)}return original(call,delay,...args)}});
  page.on('pageerror',error=>errors.push(error.message));
  await page.route('**/*',async route=>{
   const url=new URL(route.request().url());
   if(url.origin!==origin){unexpected.push(url.origin);return route.abort()}
   if(url.pathname==='/auth/api/me'){identityReads++;return route.fulfill({json:{user:{id:1,username:'fixture',role:'admin',must_change_password:false},csrf_token:'fixture-auth-csrf'}})}
   const relative=url.pathname.slice(base.length);
   if(relative==='/api/session'){sessionReads++;return route.fulfill({json:{csrf_token:'fixture-page-csrf',app:{timezone:'UTC',token_reels:true,idea_pipeline:true,read_api_version:1,rest_api_version:1,rest_api_resources:['overview','connections','pages','events','reels','video-source','idea-pipeline','telegram-approval'],rest_api_features:['idea_views']}}})}
   if(relative.startsWith('/api/')){
    if(!relative.startsWith('/api/v1/'))unexpected.push('Unversioned domain '+relative);
    if(/\/(?:avatar|image|audio|video)$/.test(relative)){media.push(relative);return route.fulfill({body:Buffer.from('<svg xmlns="http://www.w3.org/2000/svg" width="2" height="2"/>'),contentType:relative.endsWith('/image')||relative.endsWith('/avatar')?'image/svg+xml':'application/octet-stream'})}
    requests.push(relative);
    calls.push({path:relative,query:Object.fromEntries(url.searchParams)});
    if(route.request().method()!=='GET'){
     writes.push(relative);
     assert.equal(route.request().headers()['x-via-csrf'],'fixture-auth-csrf');
     assert.equal(route.request().headers()['x-mpm-csrf'],'fixture-page-csrf');
     if(relative==='/api/v1/idea-pipeline/producers/check')return route.fulfill({json:producers});
     if(relative==='/api/v1/idea-pipeline/ideas/'+ideaId+'/run'){
      holdPipeline=false;
      const run={id:'fixture-run',idea_id:ideaId,request_id:route.request().postDataJSON().request_id,status:'queued',message:'Fixture queued run'};
      runs=[run];return route.fulfill({json:{run}});
     }
     unexpected.push(route.request().method()+' '+relative);
    }
    if(forbiddenRead===relative){forbiddenRead='';return route.fulfill({status:403,json:{code:'forbidden',detail:'Fixture account is forbidden'}})}
    const delayed=holdRefresh&&['/api/v1/pages','/api/v1/connections/options'].includes(relative);
    if(delayed)await new Promise(resolve=>{releases.push(resolve);if(releases.length===2)held()});
    const window={limit:Number(url.searchParams.get('limit')||25),offset:Number(url.searchParams.get('offset')||0)};
    const values={
     '/api/v1/pages':{items:[{...pageFixture,name:delayed?'Obsolete fixture':pageFixture.name}],total:1,...window},
     '/api/v1/connections':{items:[source],total:1,...window},
     '/api/v1/connections/options':{items:[source],total:1,...window},
     '/api/v1/events':{items:[],total:0,...window},
     '/api/v1/overview':{stats:{pages:1,connections:1},updated_at:'2030-01-01T00:00:00Z'},
     '/api/v1/reels/config':config,
     '/api/v1/reels':{jobs:[],total:0,scheduler:config,...window},
     '/api/v1/reels/assets':{assets:[libraryAsset],total:1,...window},
     '/api/v1/idea-pipeline/automation':automation,
     '/api/v1/telegram-approval':telegram,
     '/api/v1/idea-pipeline/project-assets':{items:[{id:'imagegen',name:'Fixture native tool',kind:'native_codex',provider:'Codex'}]},
    };
    if(relative==='/api/v1/idea-pipeline'){
     const scoped=url.searchParams.get('view');
     assert.ok(['ideas','connections'].includes(scoped));
     const result={pipeline:{paged:true,settings:{},summary:{idea_count:1},idea_count:1,ideas:scoped==='ideas'?[idea]:[],pagination:{total:1,...window}},upload:{ready:false},video_agent:{ready:false},execution:{supported:true},policy:{production_enabled:false},runs:{items:[...runs]}};
     if(scoped==='ideas')assert.equal(url.searchParams.get('include'),null);
     else{assert.equal(url.searchParams.get('include'),'automation,telegram,audio_mixes,producers');Object.assign(result,{automation,telegram,audio_mixes:audio,producers})}
     if(holdPipeline){pipelineHeld();await new Promise(resolve=>{releasePipeline=resolve})}
     return route.fulfill({json:result}).catch(()=>{});
    }
    if(!Object.hasOwn(values,relative))unexpected.push(relative);
    return route.fulfill({status:Object.hasOwn(values,relative)?200:404,json:values[relative]||{detail:'Unknown fixture route'}}).catch(()=>{});
   }
   if(['/','/schedule','/saved','/settings'].includes(relative))return route.fulfill({path:path.join(frontend,'index.html'),contentType:'text/html'});
   if(relative.startsWith('/static/'))return route.fulfill({path:path.join(frontend,relative.slice('/static/'.length))});
   return route.fulfill({status:404,body:''});
  });

  await Promise.all([waitResponse('/api/v1/reels'),page.goto(origin+base+'/saved')]);
  await page.getByRole('heading',{name:'Không có lịch phù hợp',exact:true}).waitFor();
  assert.deepEqual(requests,['/api/v1/reels/config','/api/v1/reels'],'Saved bookmark fetches only config and saved jobs');

  let before=requests.length;
  await Promise.all([waitResponse('/api/v1/pages'),waitResponse('/api/v1/reels/assets'),nav('Lịch đăng Page').click()]);
  assert.equal(new URL(page.url()).pathname,base+'/schedule');
  assert.deepEqual(requests.slice(before).sort(),['/api/v1/pages','/api/v1/reels/assets'],'Compose only fetches the Page and asset choices');
  assert.equal(count('/api/v1/reels/config'),1,'One Reel configuration shared across both views');

  before=requests.length;
  await Promise.all([waitResponse('/api/v1/overview'),nav('Settings').click()]);
  assert.deepEqual(requests.slice(before),['/api/v1/overview']);
  before=requests.length;
  await Promise.all([waitResponse('/api/v1/connections'),page.getByRole('tab',{name:'Kết nối token',exact:true}).click()]);
  await page.getByRole('article',{name:'Kết nối Fixture token'}).waitFor();
  assert.equal(page.url(),origin+base+'/settings?tab=connections');
  assert.deepEqual(requests.slice(before),['/api/v1/connections']);
  before=requests.length;
  await Promise.all([waitResponse('/api/v1/connections'),page.reload()]);
  await page.getByRole('tab',{name:'Kết nối token',exact:true,selected:true}).waitFor();
  assert.deepEqual(requests.slice(before),['/api/v1/connections'],'Reload keeps the selected Settings tab');

  await Promise.all([waitResponse('/api/v1/pages'),waitResponse('/api/v1/connections/options'),nav('Pages').click()]);
  await page.getByRole('button',{name:'Xem chi tiết Page Fixture page',exact:true}).waitFor();
  holdRefresh=true;before=requests.length;
  await nav('Làm mới').click();await waitHeld;await nav('Làm mới').click();
  await Promise.all([waitResponse('/api/v1/connections'),nav('Settings').click()]);
  assert.deepEqual(requests.slice(before).sort(),['/api/v1/connections','/api/v1/connections/options','/api/v1/pages'],'Concurrent refreshes share pending reads');
  holdRefresh=false;releases.forEach(resolve=>resolve());
  before=requests.length;
  await Promise.all([waitResponse('/api/v1/pages'),page.goBack()]);
  await page.getByRole('button',{name:'Xem chi tiết Page Fixture page',exact:true}).waitFor();
  assert.equal(page.url(),origin+base+'/');
  assert.deepEqual(requests.slice(before),['/api/v1/pages'],'Returning to Pages reuses selector options');
  assert.equal(await page.getByText('Obsolete fixture',{exact:true}).count(),0,'Late off-screen response cannot replace the current list');

  await nav('Settings').click();
  before=requests.length;
  await Promise.all([waitResponse('/api/v1/idea-pipeline'),page.getByRole('tab',{name:'Kho nội dung',exact:true}).click()]);
  const library=page.getByRole('tabpanel',{name:'Kho nội dung',exact:true});
  await library.getByRole('heading',{name:'Fixture idea.png',exact:true}).waitFor();
  assert.deepEqual(requests.slice(before),['/api/v1/idea-pipeline'],'Ideas reads one bounded scoped window without hidden settings');
  assert.equal(calls.at(-1).query.limit,'12');assert.equal(calls.at(-1).query.view,'ideas');
  const artwork=library.getByRole('img',{name:'Thiết kế Fixture idea.png',exact:true});
  assert.equal(await artwork.getAttribute('src'),base+'/api/v1/idea-pipeline/ideas/'+ideaId+'/image');

  before=requests.length;
  await Promise.all([waitResponse('/api/v1/reels/assets'),library.getByRole('tab',{name:/^Video/}).click()]);
  await library.getByText('Fixture video.mp4',{exact:true}).waitFor();
  assert.deepEqual(requests.slice(before),['/api/v1/reels/assets'],'Video tab does not read producer, connection or Idea status');
  const assetPreview=library.locator('video').first();
  const assetPreviewUrl=await assetPreview.getAttribute('src');
  assert.equal(assetPreviewUrl,base+'/api/v1/reels/assets/'+assetId+'/video');
  await page.evaluate(url=>fetch(url),assetPreviewUrl);

  before=requests.length;
  await Promise.all([waitResponse('/api/v1/idea-pipeline'),library.getByRole('tab',{name:'Kết nối',exact:true}).click()]);
  await library.getByRole('heading',{name:'Kết nối sản xuất nội dung',exact:true}).waitFor();
  assert.deepEqual(requests.slice(before),['/api/v1/idea-pipeline'],'Visible connection panels share one scoped read');
  assert.equal(count('/api/v1/idea-pipeline/project-assets'),0,'Closed project catalog performs no read');
  const audioUrl=await library.locator('audio').getAttribute('src');
  const approvalUrl=await library.getByLabel('Video chờ duyệt Page 555',{exact:true}).getAttribute('src');
  assert.equal(audioUrl,base+'/api/v1/idea-pipeline/audio-mixes/'+mixId+'/audio');
  assert.equal(approvalUrl,base+'/api/v1/telegram-approval/requests/'+approvalId+'/video');
  await page.evaluate(urls=>Promise.all(urls.map(url=>fetch(url))),[audioUrl,approvalUrl]);
  await Promise.all([waitResponse('/api/v1/idea-pipeline/project-assets'),library.locator('.idea-project-assets summary').click()]);
  await library.getByRole('cell',{name:'Fixture native tool',exact:true}).waitFor();
  await library.locator('.idea-project-assets summary').click();await library.locator('.idea-project-assets summary').click();
  assert.equal(count('/api/v1/idea-pipeline/project-assets'),1,'Catalog is cached after explicit expansion');
  before=requests.length;
  await page.evaluate(()=>window.fixtureIdeaPoll());
  assert.deepEqual(requests.slice(before).sort(),['/api/v1/idea-pipeline/automation','/api/v1/telegram-approval'],'Settings polling reads live approval and automation state only');
  await library.getByRole('button',{name:'Kiểm tra kết nối Codex',exact:true}).click();
  assert.deepEqual(writes,['/api/v1/idea-pipeline/producers/check']);

  await Promise.all([waitResponse('/api/v1/idea-pipeline'),library.getByRole('tab',{name:/^Idea/}).click()]);
  await library.getByRole('heading',{name:'Fixture idea.png',exact:true}).waitFor();
  const waitPipelineHeld=new Promise(resolve=>{pipelineHeld=resolve});
  holdPipeline=true;before=count('/api/v1/idea-pipeline');
  await library.getByRole('button',{name:'Đọc lại idea',exact:true}).evaluate(node=>{node.click();node.click()});
  await waitPipelineHeld;
  assert.equal(count('/api/v1/idea-pipeline'),before+1,'Concurrent explicit refresh shares its current read');
  await Promise.all([waitResponse('/api/v1/idea-pipeline'),library.getByRole('button',{name:'Chạy idea Fixture idea.png',exact:true}).click()]);
  await library.locator('summary').filter({hasText:'Chi tiết idea'}).click();
  await library.getByText('Fixture queued run',{exact:true}).waitFor();
  assert.equal(count('/api/v1/idea-pipeline'),before+2,'A completed write invalidates the old read and gets authoritative state once');
  releasePipeline();
  await library.getByText('Fixture queued run',{exact:true}).waitFor();
  await Promise.all([waitResponse('/api/v1/pages'),nav('Pages').click()]);before=requests.length;
  await page.evaluate(()=>window.fixtureIdeaPoll());
  assert.equal(requests.length,before,'Unmounted library performs no polling');
  const authCounts=[identityReads,sessionReads];forbiddenRead='/api/v1/pages';
  await nav('Làm mới').click();
  await page.getByText('Fixture account is forbidden',{exact:true}).waitFor();
  assert.deepEqual([identityReads,sessionReads],authCounts,'A genuine authorization denial does not reload auth/session or retry the resource');
  assert.ok(media.every(path=>path.startsWith('/api/v1/')));
  assert.ok(media.includes('/api/v1/reels/assets/'+assetId+'/video'));
  assert.ok(media.includes('/api/v1/telegram-approval/requests/'+approvalId+'/video'));
  assert.deepEqual(unexpected,[]);assert.deepEqual(errors,[]);
  console.log('PASS: scoped REST v1 across all Page views and library panels/media, bounded visible windows, lazy project catalog, slim live polling, history/bookmarks, cached choices/config, coalesced refresh and stale isolation.');
 }finally{await browser.close()}
})().catch(error=>{console.error(error);process.exitCode=1});
