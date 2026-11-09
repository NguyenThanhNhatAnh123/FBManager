// Offline browser fixture: a stale settings editor must reload before saving.
const {chromium}=require('../frontend/node_modules/playwright')
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict')
const root=path.resolve(__dirname,'..'),dist=path.resolve(process.env.CM_UI_DIST||path.join(root,'frontend/dist'))
;(async()=>{
 const browser=await chromium.launch({channel:'chrome',headless:true})
 try{
  const page=await browser.newPage({offline:true,serviceWorkers:'block',viewport:{width:1440,height:1000}}),errors=[],outside=[],writes=[]
  const refreshRequests=[];page.on('request',request=>{const pathname=new URL(request.url()).pathname;if(pathname.endsWith('/facebook/refresh-status'))refreshRequests.push({path:pathname,method:request.method()})})
  require('./workspace_fixture.cjs').installWorkspaceFixture(page)
  page.on('pageerror',error=>errors.push(error.message))
  let settings={revision:1,max_browsers:5,max_memory_percent:85,max_cpu_percent:95,min_available_memory_mb:1024,action_delay_ms:1000,
   launch_timeout_seconds:45,task_timeout_seconds:300,require_proxy:true,proxy_ip_version:'auto',proxy_routing:'all',allowed_origins:['https://example.com'],facebook_auto_refresh:false}
  const system={cpu_percent:1,memory_percent:20,memory_used_mb:2048,memory_total_mb:16384,memory_available_mb:14336,total_profiles:0,running_browsers:0,
   queued_browsers:0,stopped_browsers:0,working_proxies:0,failed_proxies:0,total_proxies:0,running_tasks:0,pending_tasks:0,resource_warning:null,max_browsers:5,checking_proxies:0}
  await page.routeWebSocket('**/ws',socket=>socket.close())
  await page.route('**/*',async route=>{
   const request=route.request(),url=new URL(request.url()),p=url.pathname
   if(url.origin!=='http://127.0.0.1:8000'){outside.push(url.origin);return route.abort()}
   if(p.startsWith('/api/')){
    if(p==='/api/settings'&&request.method()==='PUT'){
     const body=request.postDataJSON();writes.push(body)
     if(body.revision!==settings.revision)return route.fulfill({status:409,json:{detail:'Cài đặt đã được người khác thay đổi. Tải lại trước khi lưu.'}})
     settings={...body,revision:settings.revision+1};return route.fulfill({json:settings})
    }
    assert.equal(request.method(),'GET','Only an explicit settings submission can write')
    let value
    if(p==='/api/settings')value=settings
    else if(p==='/api/system/status')value=system
    else if(p==='/api/session')value={}
    else if(['/api/profiles','/api/proxies','/api/tasks','/api/logs'].includes(p))value=[]
    else if(p==='/api/facebook/refresh-status')value={enabled:false,interval_hours:24,running:false,eligible_count:0,due_count:0,next_due_at:null,profiles:[]}
    else return route.fulfill({status:404,json:{detail:'Unrecognized isolated fixture API'}})
    return route.fulfill({json:value})
   }
   const file=path.resolve(dist,'.'+(p==='/'?'/index.html':p))
   if(!file.startsWith(dist+path.sep)||!fs.existsSync(file))return route.abort()
   return route.fulfill({path:file,contentType:file.endsWith('.js')?'application/javascript':file.endsWith('.css')?'text/css':file.endsWith('.woff2')?'font/woff2':'text/html'})
  })
  await page.goto('http://127.0.0.1:8000/')
  await page.locator('.sidebar').getByRole('button',{name:'Settings',exact:true}).click()
  await page.getByRole('tab',{name:'Cài đặt ứng dụng',exact:true}).click()
  const browsers=page.getByRole('spinbutton',{name:/^Concurrent browsers/}),delay=page.getByRole('spinbutton',{name:/^Per-origin action delay \(ms\)/})
  await browsers.waitFor();assert.equal(await browsers.inputValue(),'5')
  await browsers.fill('7')
  settings={...settings,revision:2,action_delay_ms:2500}
  await page.getByRole('button',{name:'Save settings',exact:true}).click()
  await page.getByText('Cài đặt đã được người khác thay đổi. Tải lại trước khi lưu.',{exact:true}).waitFor()
  assert.equal(writes[0].revision,1);assert.equal(settings.max_browsers,5);assert.equal(settings.action_delay_ms,2500)
  await page.getByRole('button',{name:'Tải lại cài đặt',exact:true}).click()
  await page.getByText('Đã tải lại cài đặt mới nhất.',{exact:true}).waitFor()
  assert.equal(await browsers.inputValue(),'5');assert.equal(await delay.inputValue(),'2500')
  await browsers.fill('7');await page.getByRole('button',{name:'Save settings',exact:true}).click()
  await page.getByText('Settings saved',{exact:true}).waitFor()
  assert.equal(writes.length,2);assert.equal(writes[1].revision,2);assert.equal(writes[1].action_delay_ms,2500)
  assert.equal(settings.max_browsers,7);assert.equal(settings.revision,3)
  assert.ok(refreshRequests.length>0,'Mounted Facebook settings exercise the refresh-status transport')
  assert.ok(refreshRequests.every(request=>request.path==='/api/v1/facebook/refresh-status'&&request.method==='GET'),'Status reads use the versioned resource endpoint')
  assert.deepEqual(errors,[]);assert.deepEqual(outside,[])
  console.log('PASS: identity CSRF on every write; stale settings conflict preserves the other editor, explicit reload gets latest revision, subsequent save succeeds. Offline fixtures only.')
 }finally{await browser.close()}
})().catch(error=>{console.error(error);process.exitCode=1})
