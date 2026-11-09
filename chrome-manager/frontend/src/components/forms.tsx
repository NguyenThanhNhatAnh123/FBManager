import { useState, type FormEvent } from 'react'
import { Plus, Trash2, ArrowDown, Upload, ShieldCheck } from 'lucide-react'
import { api } from '../services/api'
import type { Action, Notify, Profile, ProxyItem, TaskItem } from '../services/types'
import { Modal, Submit } from './ui'
import { Pagination } from './Pagination'
import { useServerPage } from '../hooks/useServerPage'
import { confirmedProfileIds } from '../services/profileSelection'
import { ResourceSelect } from './ResourceSelect'

type Done=()=>void
export function ProfileForm({profile,notify,done,close}:{profile?:Profile;notify:Notify;done:Done;close:Done}){
  const [name,setName]=useState(profile?.name||'')
  const [proxy,setProxy]=useState(String(profile?.proxy_id||''))
  const [revision]=useState(profile?.revision)
  const [busy,setBusy]=useState(false)
  async function submit(e:FormEvent){e.preventDefault();setBusy(true);try{await api(profile?'/profiles/'+profile.id:'/profiles',profile?'PATCH':'POST',{name,proxy_id:proxy?Number(proxy):null,...(profile?{revision}:{} )});notify(profile?'Profile updated':'Profile created');done()}catch(e){notify((e as Error).message,true)}finally{setBusy(false)}}
  return <Modal title={profile?'Edit profile':'Create a profile'} subtitle="A separate Chrome workspace with persistent browser data." close={close}>
    <form onSubmit={submit} className="form-stack"><label>Profile name<input required maxLength={80} value={name} onChange={e=>setName(e.target.value)} placeholder="e.g. QA Workspace"/></label>
    {profile?.bm?<p className="hint">Proxy BM riêng: {profile.proxy?profile.proxy.host+':'+profile.proxy.port:'Chưa gán'}. Thay hoặc gỡ bằng nút Proxy riêng trong BM Profiles.</p>:<label>Assigned proxy<ResourceSelect<ProxyItem> path="proxy-options" params={{profile_id:profile?.id}} label="Assigned proxy" value={Number(proxy)} selected={profile?.proxy} onChange={id=>setProxy(id?String(id):'')} display={p=>'#'+p.id+' · '+p.protocol+'://'+p.host+':'+p.port} emptyLabel="No proxy assigned"/></label>}
    <p className="hint">Each proxy can be assigned to one profile. Stop the browser before changing its proxy. Renaming keeps its existing data folder.</p>
    {!profile?.bm&&!proxy&&<p className="hint">Profile chưa gán proxy sẽ không hiển thị trên Dashboard.</p>}
    <div className="modal-actions"><button className="button" type="button" onClick={close}>Cancel</button><Submit busy={busy}/></div></form>
  </Modal>
}
export function ImportForm({notify,done,close}:{notify:Notify;done:Done;close:Done}){
  const [content,setContent]=useState(''),[assign,setAssign]=useState(true),[busy,setBusy]=useState(false),[report,setReport]=useState(''),[filename,setFilename]=useState(''),[protocol,setProtocol]=useState('http')
  async function readFile(file:File){
    if(file.size>2_000_000){notify('File must be smaller than 2 MB',true);return}
    if(!/\.(txt|csv)$/i.test(file.name)){notify('Choose a TXT or CSV file',true);return}
    try{setContent(await file.text());setFilename(file.name);setReport('')}catch{notify('The file could not be read',true)}
  }
  async function submit(e:FormEvent){e.preventDefault();setBusy(true);try{
    const result=await api<{created:number;assigned:number;duplicates:number;checking:number[];errors:{line:number;message:string}[]}>('/proxies/import','POST',{content,assign,default_protocol:protocol,check_after_import:true})
    const message=result.created+' imported · '+result.assigned+' assigned · '+result.duplicates+' duplicates · '+(result.checking?.length||0)+' checks queued'
    if(result.errors.length){const detail='Rejected lines: '+result.errors.map(e=>e.line).join(', ');setReport(message+'\n'+detail);notify(message+' · '+detail,true);return}
    setContent('');setFilename('');notify(message);done()
  }catch(e){notify((e as Error).message,true)}finally{setBusy(false)}}
  return <Modal title="Import proxies" subtitle="Paste a list or upload a TXT / CSV file. Maximum 1,000 rows." close={close}>
    <form onSubmit={submit} className="form-stack">
    <label className={'file-picker '+(filename?'has-file':'')} onDragOver={e=>e.preventDefault()} onDrop={e=>{e.preventDefault();const file=e.dataTransfer.files[0];if(file)void readFile(file)}}><span className="file-picker-icon"><Upload size={22}/></span><div className="file-picker-copy"><strong>{filename||'Click to upload or drag a file here'}</strong><span>TXT or CSV · Up to 1,000 proxies · 2 MB max</span></div><input type="file" aria-label="Choose TXT or CSV file" accept=".txt,.csv" onChange={e=>{const file=e.target.files?.[0];if(file)void readFile(file)}}/></label>
    <label>Proxy protocol<select aria-label="Proxy protocol" value={protocol} onChange={e=>setProtocol(e.target.value)}><option value="http">HTTP (including HTTPS websites)</option><option value="https">HTTPS proxy server</option><option value="socks5">SOCKS5</option></select><small className="hint">Applies to IP:PORT:USERNAME:PASSWORD lines. A protocol written in the file takes priority.</small></label>
    <label>Proxy list<span className="field-meta">{content.split(/\r?\n/).filter(line=>line.trim()).length} lines</span><textarea className="mono" rows={7} required value={content} onChange={e=>setContent(e.target.value)} placeholder={'host:port\nhost:port:username:password\nsocks5://username:password@host:1080'}/></label>
    <p className="hint">CSV header: host,port,username,password,protocol. Bare IP defaults to HTTP port 80. Missing SOCKS5 port defaults to 1080.</p>
    <label className="check-label"><input type="checkbox" checked={assign} onChange={e=>setAssign(e.target.checked)}/>Assign in order to profiles without a proxy</label>
    <p className="hint">Proxy hợp lệ sẽ được tự động kiểm tra sau khi thêm.</p>
    <div className="security-note"><ShieldCheck size={17}/>Credentials are encrypted with Windows DPAPI.</div>
    {report&&<pre className="result-box">{report}</pre>}
    <div className="modal-actions"><button className="button" type="button" onClick={close}>Close</button><Submit busy={busy} disabled={!content.trim()} label="Import proxies"/></div>
    </form></Modal>
}
export function RunForm({initial,taskId,notify,done,close}:{initial:number[];taskId?:number;notify:Notify;done:Done;close:Done}){
  const canRun=(p:Profile)=>p.browser_mode!=='manual'||!['Running','Starting','Queued'].includes(p.browser_status)
  const [task,setTask]=useState(taskId||0)
  const [ids,setIds]=useState(new Set(initial)),[busy,setBusy]=useState(false),[search,setSearch]=useState('')
  const [page,setPage]=useState(1),[pageSize,setPageSize]=useState(10)
  const data=useServerPage<Profile>('profiles',{scope:'regular',q:search.trim(),name_only:true},page,pageSize,setPage)
  const current=page,visible=data.items
  async function selectMatching(){setBusy(true);try{const result=await api<{ids:number[]}>('/v1/profile-ids?can_run=true&name_only=true&q='+encodeURIComponent(search.trim()));setIds(new Set(result.ids))}catch(reason){notify((reason as Error).message,true)}finally{setBusy(false)}}
  async function submit(e:FormEvent){
    e.preventDefault();setBusy(true)
    try{
      const confirmed=await confirmedProfileIds([...ids],'regular',true)
      if(confirmed.length!==ids.size){setIds(new Set(confirmed));throw new Error('Một số profile đã đổi hoặc đang bận. Kiểm tra danh sách rồi thử lại.')}
      const result=await api<{run_ids:number[]}>('/tasks/'+task+'/run','POST',{profile_ids:confirmed})
      notify(result.run_ids.length+' task runs queued');done()
    }catch(reason){notify((reason as Error).message,true)}finally{setBusy(false)}
  }
  return <Modal title="Run automation" subtitle="Profiles share the configured browser limit and per-site action rate." close={close}>
    <form onSubmit={submit} className="form-stack">
    <label>Saved task<ResourceSelect<TaskItem> path="task-options" label="Saved task" value={task} onChange={setTask} display={item=>item.name} required autoSelect emptyLabel="Select a task"/></label>
    <label>Profiles · {ids.size} selected<input aria-label="Search automation profiles" placeholder="Search profiles" value={search} onChange={e=>{setSearch(e.target.value);setPage(1)}}/></label>
    <p className="hint">Stop profiles marked Manual browsing before running a task. Saved cookies and sessions are kept.</p>
    <div className="selection-tools"><button type="button" disabled={busy} onClick={()=>void selectMatching()}>Select matching</button><button type="button" onClick={()=>setIds(new Set())}>Clear</button></div>
    {data.error&&<p className="warning" role="alert">{data.error}</p>}
    <div className="profile-picker">{visible.map(p=><label key={p.id} className="check-label"><input type="checkbox" disabled={!canRun(p)} checked={ids.has(p.id)} onChange={()=>setIds(old=>{const next=new Set(old);next.has(p.id)?next.delete(p.id):next.add(p.id);return next})}/>{p.name}<span>{!canRun(p)?'Stop manual browser first':p.proxy?'Proxy #'+p.proxy.id:'No proxy'}</span></label>)}{!data.total&&<p className="hint">No profiles match this search.</p>}</div>
    <Pagination total={data.total} page={current} pageSize={pageSize} selected={ids.size} label="automation profiles" onPageChange={setPage} onPageSizeChange={size=>{setPageSize(size);setPage(1)}}/>
    <div className="modal-actions"><button className="button" type="button" onClick={close}>Cancel</button><Submit busy={busy} disabled={!ids.size||!task} label={'Queue '+ids.size+' runs'}/></div>
    </form></Modal>
}
const actionLabels:Record<string,string>={open_url:'Open URL',wait_load:'Wait for page load',read_title:'Read page title',screenshot:'Take screenshot',click:'Click element',fill:'Fill form field',read_text:'Read element text',wait_selector:'Wait for element',scroll:'Scroll page',close_tab:'Close tab'}
export function TaskBuilder({notify,done,close}:{notify:Notify;done:Done;close:Done}){
  const [name,setName]=useState('Website Test 01'),[actions,setActions]=useState<Action[]>([{type:'open_url',url:'https://example.com'},{type:'wait_load'},{type:'read_title'},{type:'screenshot'},{type:'close_tab'}])
  const [authorized,setAuthorized]=useState(false),[closeBrowser,setCloseBrowser]=useState(true),[busy,setBusy]=useState(false)
  function update(index:number,patch:Partial<Action>){setActions(old=>old.map((a,i)=>i===index?{...a,...patch}:a))}
  async function submit(e:FormEvent){e.preventDefault();setBusy(true);try{await api('/tasks','POST',{name,authorized,close_browser:closeBrowser,actions});notify('Task created');done()}catch(e){notify((e as Error).message,true)}finally{setBusy(false)}}
  return <Modal wide title="Build an automation task" subtitle="A guided sequence for websites you own or are authorized to test." close={close}>
    <form onSubmit={submit} className="form-stack"><label>Task name<input value={name} onChange={e=>setName(e.target.value)} required maxLength={120}/></label>
    <div className="builder-label">ACTION SEQUENCE <span>{actions.length} steps</span></div>
    <div className="steps">{actions.map((action,i)=><div className="step" key={i}><span className="step-number">{i+1}</span><div className="step-fields"><select aria-label={'Action '+(i+1)} value={action.type} onChange={e=>{const type=e.target.value;setActions(old=>old.map((a,j)=>j===i?{type,...(type==='open_url'?{url:'https://example.com'}:{})}:a))}}>{Object.entries(actionLabels).map(([value,label])=><option key={value} value={value}>{label}</option>)}</select>
    {action.type==='open_url'&&<input aria-label="Target URL" required type="url" placeholder="https://your-site.com" value={action.url||''} onChange={e=>update(i,{url:e.target.value})}/>}
    {['click','fill','read_text','wait_selector'].includes(action.type)&&<input aria-label="CSS selector" required placeholder="CSS selector, e.g. #email" value={action.selector||''} onChange={e=>update(i,{selector:e.target.value})}/>}
    {action.type==='fill'&&<input aria-label="Field value" required placeholder="Test value (encrypted at rest)" value={action.value||''} onChange={e=>update(i,{value:e.target.value})}/>}
    {action.type==='scroll'&&<input aria-label="Scroll pixels" type="number" min={-10000} max={10000} value={action.pixels??700} onChange={e=>update(i,{pixels:Number(e.target.value)})}/>}
    </div><div className="step-controls"><button className="icon-button" type="button" title="Move down" disabled={i===actions.length-1} onClick={()=>setActions(old=>{const n=[...old];[n[i],n[i+1]]=[n[i+1],n[i]];return n})}><ArrowDown size={15}/></button><button className="icon-button danger-text" type="button" title="Remove step" disabled={actions.length===1} onClick={()=>setActions(old=>old.filter((_,j)=>j!==i))}><Trash2 size={15}/></button></div></div>)}</div>
    <button type="button" className="button dashed" disabled={actions.length>=50} onClick={()=>setActions(old=>[...old,{type:'read_title'}])}><Plus size={16}/>Add action</button>
    <p className="hint">Approve each required origin in Settings, including redirects and asset hosts. Actions stop on access-denied or rate-limit responses. Minimum action delay is enforced across profiles.</p>
    <label className="check-label"><input type="checkbox" checked={closeBrowser} onChange={e=>setCloseBrowser(e.target.checked)}/>Close the browser after each run, keeping profile data</label>
    <label className="check-label authorization"><input required type="checkbox" checked={authorized} onChange={e=>setAuthorized(e.target.checked)}/>I own these websites or have permission to run these tests.</label>
    <div className="modal-actions"><button className="button" type="button" onClick={close}>Cancel</button><Submit busy={busy} label="Create task"/></div>
    </form></Modal>
}

