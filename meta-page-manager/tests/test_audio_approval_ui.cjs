// Offline UI only: no real song, approval, producer, uploader or Meta requests.
const {chromium}=require('../../chrome-manager/frontend/node_modules/playwright');
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const frontend=path.resolve(__dirname,'../frontend'),base=process.argv.includes('--mounted')?'/pages':'',origin=base?'http://127.0.0.1:8000':'http://127.0.0.1:8010';
const copy=value=>JSON.parse(JSON.stringify(value)),hex=i=>i.toString(16).padStart(64,'0');
const pending=()=>({source_directory:'Downloads/FILE ÂM THANH',render_signature:'c'.repeat(64),ready:false,source_count:6,approved_count:0,blockers:[],items:Array.from({length:6},(_,i)=>({id:hex(i+1),source_filename:'Track '+(i+1)+'.mp3',source_sha256:'a'.repeat(64),pcm_sha256:'b'.repeat(64),duration_seconds:8,sample_rate:48000,channels:2,status:'pending',prepared:true,approved_at:null,preview_url:'/api/idea-pipeline/audio-mixes/'+hex(i+1)+'/audio'}))});
(async()=>{
 const browser=await chromium.launch({channel:'chrome',headless:true});
 try{
  const context=await browser.newContext({offline:true,serviceWorkers:'block',viewport:{width:1100,height:1000}}),page=await context.newPage();
  const writes=[],reads=[],media=[],outside=[],errors=[];let value=pending(),release=null,mode='success',sessions=0;
  await page.route('**/*',async route=>{
   const request=route.request(),url=new URL(request.url());
   if(url.origin===origin&&url.pathname==='/auth/api/me')return route.fulfill({json:{user:{id:1,username:'fixture',role:'admin',must_change_password:false},csrf_token:'fixture-via-csrf'}});
   if(url.origin===origin&&/^(?:\/pages)?\/api\//.test(url.pathname)&&!['GET','HEAD','OPTIONS'].includes(request.method()))assert.equal(request.headers()['x-via-csrf'],'fixture-via-csrf');

   if(url.origin!==origin||!url.pathname.startsWith(base+'/')){outside.push(url.href);return route.abort()}
   const p=url.pathname.slice(base.length);
   if(p==='/')return route.fulfill({contentType:'text/html',body:'<!doctype html><html><head><link rel="stylesheet" href="'+base+'/static/styles.css"><link rel="stylesheet" href="'+base+'/static/idea-pipeline.css"></head><body><main style="max-width:850px;padding:15px;margin:auto"></main><script type="module">import {createAudioMixes} from "'+base+'/static/audio-mixes.js";window.panel=createAudioMixes();document.querySelector("main").append(panel.node)</script></body></html>'});
   if(p.startsWith('/static/')){const file=path.resolve(frontend,p.slice(8));assert.ok(file.startsWith(frontend+path.sep));return route.fulfill({path:file,contentType:file.endsWith('.js')?'application/javascript':file.endsWith('.woff2')?'font/woff2':'text/css'})}
   if(p==='/api/session'){sessions++;return route.fulfill({json:{csrf_token:'fixture-audio-'+sessions,app:{}}})}
   if(p==='/api/idea-pipeline/audio-mixes'){assert.equal(request.method(),'GET');reads.push(p);return route.fulfill({json:copy(value)})}
   if(p==='/api/idea-pipeline/audio-mixes/approve'){
    assert.equal(request.method(),'POST');assert.equal(request.headers()['x-mpm-csrf'],'fixture-audio-'+sessions);
    const body=request.postDataJSON();writes.push(body);assert.deepEqual(Object.keys(body),['mix_ids','render_signature']);
    await new Promise(resolve=>{release=resolve});
    if(mode==='403')return route.fulfill({status:403,json:{detail:'Expired fixture'}});
    assert.equal(body.render_signature,value.render_signature);
    for(const row of value.items)if(body.mix_ids.includes(row.id))Object.assign(row,{status:'approved',approved_at:'2030-01-01T12:00:00+00:00'});
    value.approved_count=value.items.filter(row=>row.status==='approved').length;value.ready=value.approved_count===value.source_count;
    if(mode==='unknown')return route.abort('failed');
    return route.fulfill({json:copy(value)});
   }
   if(/^\/api\/idea-pipeline\/audio-mixes\/[a-f0-9]{64}\/audio$/.test(p)){media.push(p);return route.fulfill({status:404,body:''})}
   throw Error('Unexpected fixture request '+request.method()+' '+p);
  });
  page.on('pageerror',error=>errors.push(error.message));page.setDefaultTimeout(7000);
  await page.goto(origin+base+'/');await page.getByRole('heading',{name:'Nghe và chấp nhận nhạc',exact:true}).waitFor();
  const save=()=>page.getByRole('button',{name:'Xác nhận các bản đã nghe',exact:true}),check=i=>page.getByRole('checkbox',{name:'Đã nghe và chấp nhận bản mix Track '+i+'.mp3',exact:true});
  assert.deepEqual(reads,[]);assert.deepEqual(writes,[]);assert.deepEqual(media,[]);
  await page.evaluate(value=>panel.adopt(value),{...copy(value),render_signature:null,source_count:0,items:[]});
  await page.getByText('Nguồn nhạc: Downloads/FILE ÂM THANH',{exact:true}).waitFor();assert.equal(await save().isDisabled(),true);
  const notPrepared=copy(value);for(const row of notPrepared.items)Object.assign(row,{status:'not_prepared',prepared:false,pcm_sha256:null,preview_url:null});
  await page.evaluate(value=>panel.adopt(value),notPrepared);
  assert.equal(await page.locator('audio').count(),0);assert.equal(await page.getByRole('checkbox').count(),0);
  await page.evaluate(value=>panel.adopt(value),copy(value));
  assert.equal(await page.locator('audio').count(),6);assert.equal(await page.getByRole('checkbox').count(),6);
  assert.equal(await save().isDisabled(),true);assert.equal(await check(1).isChecked(),false);
  assert.deepEqual(await page.locator('audio').evaluateAll(rows=>rows.map(row=>({autoplay:row.autoplay,preload:row.preload,controls:row.controls,src:row.getAttribute('src')}))),value.items.map(row=>({autoplay:false,preload:'none',controls:true,src:base+row.preview_url})));
  await page.locator('audio').first().dispatchEvent('ended');
  assert.equal(await check(1).isChecked(),false);assert.deepEqual(writes,[],'Playback completion cannot approve a mix');
  await page.locator('audio').first().evaluate(node=>node.dataset.fixtureIdentity='same-player');
  await check(1).check();await check(2).check();
  assert.equal(await page.locator('audio').first().getAttribute('data-fixture-identity'),'same-player','Checking a row preserves audio players');
  for(let i=0;i<3;i++)await page.evaluate(value=>panel.adopt(value),copy(value));
  assert.deepEqual(reads,[]);assert.deepEqual(writes,[]);assert.deepEqual(media,[],'Opening/adopting never autoplays or preloads songs');
  await save().evaluate(node=>{node.click();node.click()});await page.getByRole('button',{name:'Đang lưu xác nhận…',exact:true}).waitFor();
  for(let i=0;i<100&&!release;i++)await page.waitForTimeout(10);assert.ok(release);assert.equal(writes.length,1);
  assert.deepEqual(writes[0],{mix_ids:[hex(1),hex(2)],render_signature:'c'.repeat(64)});release();release=null;
  await page.getByText('Đã lưu chấp nhận cho đúng các bản mix đã chọn.',{exact:true}).waitFor();
  assert.equal(await page.getByRole('checkbox').count(),4);assert.equal(value.approved_count,2);assert.equal(value.ready,false);
  mode='unknown';await check(3).check();await save().click();for(let i=0;i<100&&!release;i++)await page.waitForTimeout(10);assert.ok(release);release();release=null;
  await page.getByRole('alert').getByText(/Chưa rõ kết quả lưu xác nhận/).waitFor();assert.equal(await save().isDisabled(),true);
  await page.evaluate(value=>panel.adopt(value),copy(value));assert.equal(writes.length,2,'Board refresh never replays an uncertain approval');
  await page.getByRole('button',{name:'Đọc lại để đối chiếu',exact:true}).click();await page.getByText(/Đã đọc lại trạng thái/).waitFor();
  assert.equal(await page.getByRole('checkbox').count(),3);assert.equal(reads.length,1);assert.equal(writes.length,2);
  await check(4).check();value.render_signature='d'.repeat(64);await page.evaluate(value=>panel.adopt(value),copy(value));
  await page.getByRole('alert').getByText(/Nguồn hoặc bản mix đã đổi/).waitFor();assert.equal(await save().isDisabled(),true);
  await page.getByRole('button',{name:'Đọc lại để đối chiếu',exact:true}).click();await page.getByText(/Đã đọc lại trạng thái/).waitFor();assert.equal(await check(4).isChecked(),false);
  // Reattach after the board releases media sources. Restore only safe local URLs.
  await page.evaluate(value=>{panel.dispose();for(const audio of panel.node.querySelectorAll('audio'))audio.removeAttribute('src');panel.node.remove();panel.adopt(value);document.querySelector('main').append(panel.node)},copy(value));
  assert.equal(await page.locator('audio').first().getAttribute('src'),base+value.items[0].preview_url);
  mode='403';await check(4).check();await save().click();for(let i=0;i<100&&!release;i++)await page.waitForTimeout(10);assert.ok(release);release();release=null;
  await page.getByRole('alert').getByText(/Phiên làm việc đã được làm mới/).waitFor();assert.equal(sessions,2);assert.equal(writes.length,3,'CSRF refresh never replays approval');
  assert.equal(await page.evaluate(()=>localStorage.length+sessionStorage.length),0);
  await page.setViewportSize({width:390,height:844});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
  assert.deepEqual(outside,[]);assert.deepEqual(errors,[]);
  console.log('Audio approval UI '+(base||'standalone')+': pending default, exact safe preview, explicit partial approval, no autoplay/replay, signature conflict and remount passed.');
 }finally{await browser.close()}
})().catch(error=>{console.error(error);process.exitCode=1});
