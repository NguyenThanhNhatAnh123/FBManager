// Adapter for older offline UI scenarios. Production clients never use these arrays.
// It projects each scenario's synthetic legacy data into the new paged API contract.
const FIXTURE_IDENTITY_CSRF='isolated-admin-csrf-fixture';
const smallVersionedReads=new Set(['system/status','settings','tasks/history','facebook/refresh-status',
 'reels/config','reels/pages','reels/page-eligibility','page-recipient/config']);
function installIdentityFixture(page){
 const register=page.route.bind(page);
 page.route=(pattern,handler,...args)=>register(pattern,async route=>{
  const request=route.request(),url=new URL(request.url());
  if(url.pathname==='/auth/api/me')return route.fulfill({json:{user:{id:1,username:'fixture-admin',role:'admin',active:true,must_change_password:false,revision:1},csrf_token:FIXTURE_IDENTITY_CSRF}});
  if(url.pathname.startsWith('/api/')&&!['GET','HEAD','OPTIONS'].includes(request.method())){
   require('node:assert/strict').equal(request.headers()['x-via-csrf'],FIXTURE_IDENTITY_CSRF,'Every offline API write must carry the identity CSRF header');
  }
  return handler(route);
 },...args);
}
function installWorkspaceFixture(page){
 installIdentityFixture(page);
 const register=page.route.bind(page);
 let recipientRows=[];
 page.route=(pattern,handler,...args)=>register(pattern,async route=>{
  const request=route.request(),actual=new URL(request.url());
  const versioned=actual.pathname.startsWith('/api/v1/');
  const params=actual.searchParams,part=actual.pathname.slice(versioned?'/api/v1/'.length:'/api/workspace/'.length);
  function withUrl(url){
   const fakeRequest=new Proxy(request,{get(target,key){if(key==='url')return()=>url.href;const value=target[key];return typeof value==='function'?value.bind(target):value}});
   return new Proxy(route,{get(target,key){if(key==='request')return()=>fakeRequest;const value=target[key];return typeof value==='function'?value.bind(target):value}});
  }
  if(request.isNavigationRequest()&&['/chrome/pages','/chrome/reels/new','/chrome/reels/saved','/chrome/settings'].includes(actual.pathname)){
   return handler(withUrl(new URL('/'+actual.search,actual.origin)));
  }
  if(!versioned&&!actual.pathname.startsWith('/api/workspace/'))return handler(route);
  if(versioned&&(request.method()!=='GET'||smallVersionedReads.has(part)||/^reels\/assets\/[A-Za-z0-9_-]+\/video$/.test(part))){
   return handler(withUrl(new URL('/api/'+part+actual.search,actual.origin)));
  }
  async function read(path){
   let result;
   const fakeRoute=new Proxy(withUrl(new URL(path,actual.origin)),{get(target,key){if(key==='fulfill')return async value=>{result=value.status>=400?{fixtureError:value}:value.json;if(path.startsWith('/api/page-recipient/jobs')&&Array.isArray(result))recipientRows=result;return undefined};const value=target[key];return typeof value==='function'?value.bind(target):value}});
   await handler(fakeRoute);return result;
  }
  const profiles=()=>read('/api/profiles'),limit=Number(params.get('limit')||10),offset=Number(params.get('offset')||0),q=(params.get('q')||'').trim().toLowerCase(),status=params.get('status')||'all';
  const match=(...values)=>values.join(' ').toLowerCase().includes(q),all=s=>['all','All statuses',''].includes(s);
  const displayPage=p=>params.has('zone')&&p.timezone&&p.timezone!==params.get('zone')&&p.status!=='checking'?{...p,status:'unchecked',posts_today:null,posts:[]}:p;
  const pack=(items,summary)=>{const total=items.length,start=versioned?offset:Math.min(offset,Math.max(0,Math.floor((total-1)/limit)*limit));return {items:items.slice(start,start+limit),total,limit,offset:start,summary}};
  const thin=p=>({...p,facebook:p.facebook?{...p.facebook,pages:[],page_count:p.facebook.pages.length}:null,bm:p.bm?{...p.bm,portfolios:[]}:null});
  let value;
  if(part==='profile-options'){
   const scope=params.get('scope')||'regular';value=pack(((await profiles())||[]).filter(p=>!p.bm&&(scope!=='proxy'||p.proxy_id!==null)&&match(p.name,p.id,p.facebook?.name)).map(thin));
  }else if(part==='proxy-options'){
   const edited=Number(params.get('profile_id')||0),allProfiles=(await profiles())||[],assigned=new Set(allProfiles.filter(p=>p.id!==edited&&p.proxy_id).map(p=>p.proxy_id));
   value=pack(((await read('/api/proxies'))||[]).filter(p=>!assigned.has(p.id)&&match(p.id,p.host,p.port,p.protocol)));
  }else if(part==='task-options')value=pack(((await read('/api/tasks'))||[]).filter(p=>match(p.id,p.name)));
  else if(part==='task-history'||/^task-runs\/\d+$/.test(part)){
   const result=await read('/api/tasks/history'),rows=result?.items||[];
   value=part==='task-history'?pack(rows.filter(row=>(all(status)||row.status===status)&&match(row.id,row.profile_name)).map(row=>({...row,result:[]}))):rows.find(row=>row.id===Number(part.split('/')[1]));
  }else if(part==='reel-page-options'){
   const rows=await read('/api/reels/pages?profile_id='+params.get('profile_id'));
   if(rows?.fixtureError)return route.fulfill(rows.fixtureError);
   value=pack((rows||[]).filter(row=>match(row.page_name,row.page_url)));
  }else if(part==='options'){
   const include=(params.get('include')||'profiles,proxies,tasks').split(','),scope=params.get('profile_scope')||'all';
   value={};
   if(include.includes('profiles'))value.profiles=((await profiles())||[]).filter(p=>scope==='all'||!p.bm&&(scope!=='proxy'||p.proxy_id!==null)).map(thin);
   if(include.includes('proxies'))value.proxies=((await read('/api/proxies'))||[]).filter(proxy=>!proxy.profile_name);
   if(include.includes('tasks'))value.tasks=((await read('/api/tasks'))||[]).map(task=>({...task,actions:[]}));
  }
  else if(/^profiles\/\d+$/.test(part)){value=(await profiles()).find(p=>p.id===Number(part.split('/')[1]));if(value)value={...thin(value),recipient_active:recipientRows.some(row=>row.profile_id===value.id&&row.active)}}
  else if(part==='profiles'||part==='profile-ids'){
   let rows=await profiles();const scope=params.get('scope')||'regular';rows=rows.filter(p=>scope==='all'||scope==='bm'?scope==='all'||!!p.bm:!p.bm&&(scope!=='proxy'||p.proxy_id!==null));
   const summary={all:rows.length,running:rows.filter(p=>p.browser_status==='Running').length,stopped:rows.filter(p=>p.browser_status==='Stopped').length,portfolios:rows.reduce((n,p)=>n+(p.bm?.portfolios.length||0),0)};
   rows=rows.filter(p=>(all(status)||p.browser_status===status)&&(params.get('name_only')==='true'?match(p.name):match(p.name,p.id,p.facebook?.name,p.proxy?.host,p.proxy?.ip_address,p.bm?.note,...(p.bm?.portfolios||[]).flatMap(b=>[b.bm_id,b.name]),...(p.facebook?.pages||[]).map(p=>p.name))));
   const post=params.get('post_status');if(post&&post!=='all')rows=rows.filter(p=>post==='unchecked'?!p.facebook?.pages.length||p.facebook.pages.some(p=>['unchecked','unknown'].includes(p.status)):p.facebook?.pages.some(p=>p.status===post));
   if(part==='profile-ids'){
    const selected=params.has('ids')?new Set(params.get('ids').split(',').map(Number)):null;
    const ids=rows.filter(p=>(!selected||selected.has(p.id))&&(!params.has('can_run')||p.browser_mode!=='manual'||!['Running','Starting','Queued'].includes(p.browser_status))).map(p=>p.id);
    value={ids,total:ids.length};
   }else{
    const preview=Number(params.get('preview')??(versioned?0:3));
    value=pack(rows.map(p=>({...p,facebook:p.facebook?{...p.facebook,pages:p.facebook.pages.slice(0,preview).map(displayPage),page_count:p.facebook.pages.length}:null})),summary);
   }
  }else if(part==='facebook-pages'){
   const p=(await profiles()).find(p=>p.id===Number(params.get('profile_id'))),rows=p?.facebook?.pages||[];value=pack(rows.filter(p=>(all(status)||p.status===status)&&match(p.name,p.id,p.url)),{all:rows.length});
  }else if(part==='proxies'){const rows=await read('/api/proxies');value=pack(rows.filter(p=>(all(status)||(p.checking?'Checking':p.status)===status)&&match(p.id,p.host,p.port,p.ip_address,p.protocol,p.profile_name)),{all:rows.length})}
  else if(part==='tasks'){const rows=await read('/api/tasks');value=pack(rows.filter(t=>match(t.id,t.name)))}
  else if(part==='logs'){const rows=await read('/api/logs?profile_id='+params.get('profile_id')),level=params.get('level')||'all';value=pack(rows.filter(r=>(level==='all'||r.level===level)&&match(r.id,r.level,r.message)),{levels:[...new Set(rows.map(r=>r.level))]})}
  else if(part==='recipient-jobs'){
   const [rows,p]=await Promise.all([read('/api/page-recipient/jobs'),profiles()]);value=pack(rows.filter(r=>(all(status)||r.status===status)&&match(r.profile_name,r.profile_id,r.page_name,r.page_id,r.message)).sort((a,b)=>Date.parse(b.updated_at)-Date.parse(a.updated_at)).map(r=>({...r,profile:p.find(p=>p.id===r.profile_id)})),{all:rows.length,statuses:[...new Set(rows.map(r=>r.status))]});
  }else if(part==='reels'||/^reels\/\d+$/.test(part)){
   const [rows,assets]=await Promise.all([read('/api/reels'+(part!=='reels'||params.get('include_archived')==='true'?'?include_archived=true':'')),read('/api/reels/assets')]);
   const valid=rows.map(r=>{const asset=assets.find(a=>a.id===r.asset_id);return {...r,asset_preview_url:asset?.preview_url}});
   if(part!=='reels')value=valid.find(r=>r.id===Number(part.split('/')[1]));
   else {
    const calendar=params.get('calendar')||'',sort=params.get('sort')||'recent';
    const calendarKey=row=>{try{const parts=new Intl.DateTimeFormat('en-GB',{timeZone:row.timezone,year:'numeric',month:'2-digit',day:'2-digit'}).formatToParts(new Date(row.scheduled_at));return ['year','month','day'].map(key=>parts.find(part=>part.type===key).value).join('-')}catch{return ''}};
    const matching=valid.filter(r=>(params.get('include_archived')==='true'||!r.archived_at)&&(all(status)||r.status===status)&&match(r.id,r.profile_name,r.page_name,r.page_url,r.asset_name,r.caption)&&(!calendar||calendarKey(r).startsWith(calendar)));
    matching.sort(sort==='scheduled_asc'?(a,b)=>Date.parse(a.scheduled_at)-Date.parse(b.scheduled_at):sort==='scheduled_desc'?(a,b)=>Date.parse(b.scheduled_at)-Date.parse(a.scheduled_at):(a,b)=>Date.parse(b.created_at)-Date.parse(a.created_at));
    value=pack(matching,{all:rows.length});
   }
  }else if(part==='reel-assets'){const rows=await read('/api/reels/assets');value=pack(rows.filter(r=>match(r.id,r.name)))}
  else if(part==='authenticator'){const old=await read('/api/extensions/authenticator');value=pack(old.profiles,old)}
  else if(part==='summary'){const rows=(await profiles()).filter(p=>!p.bm&&p.proxy_id!==null),pages=rows.flatMap(p=>p.facebook?.pages||[]),posted=pages.filter(p=>p.status==='posted').length,missing=pages.filter(p=>p.status==='not_seen').length;value={accounts:rows.filter(p=>p.facebook?.name).length,pages:pages.length,posted,missing,pending:pages.length-posted-missing,active:rows.filter(p=>['queued','checking'].includes(p.facebook?.status)).length}}
  else if(part==='pages'){
   const rows=await profiles(),groups=new Map();
   for(const p of rows)for(const f of p.facebook?.pages||[]){let key=f.url,url=f.url;try{const parsed=new URL(f.url),path=parsed.pathname.replace(/^\/|\/$/g,''),id=path==='profile.php'?parsed.searchParams.get('id'):/^\d+$/.test(path)?path:null;if(id){key='id:'+id;url='https://www.facebook.com/profile.php?id='+id}}catch{}
    const group=groups.get(key)||{key,name:f.name,url,display:f,sources:[]};if(!group.sources.some(s=>s.profile.id===p.id))group.sources.push({profile:thin(p),page:f,fresh:true});if((f.checked_at||'')>(group.display.checked_at||'')){group.name=f.name;group.display=f}groups.set(key,group)}
   const catalog=[...groups.values()].sort((a,b)=>a.name.localeCompare(b.name));value=pack(catalog.filter(r=>(all(status)||r.display.status===status)&&match(r.name,r.url,...r.sources.flatMap(s=>[s.profile.name,s.profile.facebook?.name]))),{all:catalog.length,profiles:new Set(catalog.flatMap(r=>r.sources.map(s=>s.profile.id))).size,posted:catalog.filter(r=>r.display.status==='posted').length});
  }
  if(value===undefined)return route.fulfill({status:404,json:{detail:'Missing offline workspace fixture: '+part}});
  return route.fulfill({json:value});
 },...args);
}
module.exports={installWorkspaceFixture,installIdentityFixture,FIXTURE_IDENTITY_CSRF};
