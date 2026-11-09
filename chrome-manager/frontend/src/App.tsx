import { useCallback, useEffect, useState } from 'react'
import { LayoutDashboard, Globe, Cable, Workflow, Settings, PanelLeftClose, PanelLeftOpen, Plus, Upload, Play, Square, RefreshCw, ShieldCheck, ArrowUpRight, Layers, MemoryStick, CheckCircle2, AlertCircle, X, Zap, CircleHelp, CalendarClock, Mail, Film } from 'lucide-react'
import { useManager } from './hooks/useManager'
import { api } from './services/api'
import type { Profile, Log, TaskItem } from './services/types'
import { Badge, Empty, Modal } from './components/ui'
import { ProfileForm, ImportForm, RunForm, TaskBuilder } from './components/forms'
import { ProfileTable } from './components/ProfileTable'
import { SettingsPage } from './pages/Settings'
import { HistoryPage } from './pages/History'
import { ProxyPage } from './pages/Proxies'
import { Pagination } from './components/Pagination'
import { AuthenticatorDialog } from './components/AuthenticatorDialog'
import { FacebookOverview, FacebookDialog } from './components/Facebook'
import { facebookDay } from './services/facebook'
import { OutlookDialog } from './components/OutlookDialog'
import { BMProfilesPage } from './pages/BMProfiles'
import { PageRecipientPage } from './pages/PageRecipient'
import { ReelSchedulerPage } from './pages/ReelScheduler'
import { ProductSwitcher } from './components/ProductSwitcher'
import { WorkspaceSettings, type SettingsSection } from './pages/WorkspaceSettings'
import { ChromePages } from './pages/ChromePages'
import { ActivityLogDialog } from './components/ActivityLogDialog'
import { useServerPage, useServerRecord } from './hooks/useServerPage'
import { IdentityAccount } from './components/IdentityAccount'
import { chromeLocation, chromeUrl, type WorkspacePage } from './services/navigation'
import { confirmedProfileIds } from './services/profileSelection'

const navigation=[{name:'Dashboard',icon:LayoutDashboard},{name:'Pages',icon:Layers},{name:'Lịch đăng Page',icon:CalendarClock},{name:'Lịch đã lưu',icon:Film}] as const

export default function App(){
 const {system,connected,error,refresh}=useManager()
 const [screen,setScreen]=useState(()=>chromeLocation().page)
 const [settingsSection,setSection]=useState<SettingsSection>(()=>chromeLocation().settingsSection||'app')
 const [selected,setSelected]=useState(new Set<number>())
 const page=screen
 function navigate(nextPage:WorkspacePage,nextSection:SettingsSection=settingsSection){
  const url=chromeUrl(nextPage,nextSection)
  if(location.pathname+location.search!==url)history.pushState(null,'',url)
  setScreen(nextPage)
  if(nextPage==='Settings')setSection(nextSection)
 }
 function setPage(next:WorkspacePage){navigate(next)}
 function setSettingsSection(next:SettingsSection){navigate('Settings',next)}
 useEffect(()=>{
  const backOrForward=()=>{
   const next=chromeLocation()
   setScreen(next.page)
   if(next.settingsSection)setSection(next.settingsSection)
  }
  window.addEventListener('popstate',backOrForward)
  return()=>window.removeEventListener('popstate',backOrForward)
 },[])
 const [compact,setCompact]=useState(()=>localStorage.getItem('cm_sidebar_compact')==='true')
 function toggleSidebar(){setCompact(value=>{localStorage.setItem('cm_sidebar_compact',String(!value));return !value})}
 const [modal,setModal]=useState<string|null>(null),[edit,setEdit]=useState<Profile|undefined>(),[deleting,setDeleting]=useState<Profile|null>(null)
 const [facebookId,setFacebookId]=useState<number|null>(null),[outlookId,setOutlookId]=useState<number|undefined>()
 const [facebookTimezone,setFacebookTimezone]=useState(()=>{const zone=localStorage.getItem('cm_facebook_timezone')||'America/Los_Angeles';try{facebookDay(zone);return zone}catch{return 'America/Los_Angeles'}})
 function changeFacebookTimezone(zone:string){localStorage.setItem('cm_facebook_timezone',zone);setFacebookTimezone(zone)}
 const [chosenTask,setChosenTask]=useState<number|undefined>(),[runIds,setRunIds]=useState<number[]>([]),[profileLogs,setProfileLogs]=useState<{profile:Profile;logs:Log[]}|null>(null)
 const [toast,setToast]=useState<{text:string;error:boolean}|null>(null),[busy,setBusy]=useState(0)
 useEffect(()=>{if(!toast)return;const timer=setTimeout(()=>setToast(null),8000);return()=>clearTimeout(timer)},[toast])
 const notify=useCallback((text:string,error=false)=>{setToast({text,error})},[])
 async function act(path:string,data?:unknown,method='POST'){
  setBusy(n=>n+1)
  try{
   if(path==='/profiles/start-selected'||path==='/profiles/stop-selected'||path==='/facebook/check'){
    const selection=data as {profile_ids:number[]}
    const ids=await confirmedProfileIds(selection.profile_ids,'proxy')
    if(ids.length!==selection.profile_ids.length){
     const missing=selection.profile_ids.filter(id=>!ids.includes(id))
     setSelected(current=>new Set([...current].filter(id=>!missing.includes(id))))
     throw new Error('Một số profile đã đổi hoặc không còn proxy. Kiểm tra danh sách rồi thử lại.')
    }
   }
   const result=await api<{created?:number;errors?:{message:string}[];accepted?:number[]}>(path,method,data)
   if(result.errors?.length)notify(result.errors.length+' operation(s) rejected: '+result.errors[0].message,true)
   else notify(path==='/facebook/check'?`Đã xếp hàng kiểm tra Facebook cho ${result.accepted?.length||0} profile`:result.created!==undefined?result.created+' profiles created':path.includes('/start')?'Browser launch queued':path.includes('/check')?'Proxy checks queued':'Operation completed')
   await refresh();return !result.errors?.length
  }catch(e){notify((e as Error).message,true);return false}finally{setBusy(n=>n-1)}
 }
 function run(ids:number[],taskId?:number){setChosenTask(taskId);setRunIds(ids);setModal('run')}
 function create(){setEdit(undefined);setModal('profile')}
 function completed(){setModal(null);void refresh()}
 async function showLogs(profile:Profile){setProfileLogs({profile,logs:[]})}
 function checkFacebook(ids:number[]){try{void act('/facebook/check',{profile_ids:ids,...facebookDay(facebookTimezone)})}catch(e){notify((e as Error).message,true)}}
 function facebookView(profile:Profile):Profile{return profile.facebook?{...profile,facebook:{...profile.facebook,fresh:profile.facebook.fresh&&profile.facebook.timezone===facebookTimezone,pages:profile.facebook.pages.map(row=>row.timezone&&row.timezone!==facebookTimezone&&row.status!=='checking'?{...row,status:'unchecked',posts_today:null,posts:[]}:row)}}:profile}
 const {value:facebookProfile}=useServerRecord<Profile>(facebookId===null?null:'profiles/'+facebookId)
 const listedSelected=selected
 const subtitles:Record<WorkspacePage,string>={Dashboard:'Quản lý trực tiếp Chrome profile, phiên Facebook và kết nối của bạn.',Pages:'Các Page đã ghi nhận từ phiên Facebook trong Chrome profile.',
  'Lịch đăng Page':'Lên lịch video và caption cho Page bằng đúng Chrome profile.','Lịch đã lưu':'Xem video, giờ đăng và tiến trình lịch của từng Chrome profile.',Settings:'Quản lý profile, kết nối, công cụ và cấu hình ứng dụng.'}
 const [taskPage,setTaskPage]=useState(1),[taskPageSize,setTaskPageSize]=useState(10)
 const taskData=useServerPage<TaskItem>('tasks',{},taskPage,taskPageSize,setTaskPage,page==='Settings'&&settingsSection==='automation')
 const currentTaskPage=taskPage,visibleTasks=taskData.items
 function profilePanel(){
  return <>
   <div className="bulk-toolbar"><div className="toolbar-group"><button className="button small" onClick={()=>setModal('import')}><Upload size={15}/>Import Proxies</button><span className="toolbar-divider"/><button className="button small start-selected" disabled={!listedSelected.size||!!busy} onClick={()=>void act('/profiles/start-selected',{profile_ids:[...listedSelected]})}><Play size={14}/>Start Selected</button><button className="button small" disabled={!listedSelected.size||!!busy} onClick={()=>void act('/profiles/stop-selected',{profile_ids:[...listedSelected]})}><Square size={13}/>Stop Selected</button><button className="button small" disabled={!listedSelected.size} onClick={()=>run([...listedSelected])}><Zap size={15}/>Run Task Selected</button></div><button className="text-button" disabled={!system?.total_proxies||!!busy} onClick={()=>void act('/proxies/check',{})}><RefreshCw size={14}/>Check All Proxies</button></div>
   <ProfileTable proxyOnly timezone={facebookTimezone} selected={listedSelected} setSelected={setSelected} act={(path,data)=>void act(path,data)} edit={profile=>{setEdit(profile);setModal('profile')}} remove={setDeleting} run={run} logs={profile=>void showLogs(profile)} create={create} facebook={profile=>setFacebookId(profile.id)} outlook={profile=>{setOutlookId(profile.id);setModal('outlook')}}/>
   {system?.proxy_routing==='facebook_only'&&<div className="info-banner"><Cable size={20}/><div><strong>Facebook only · Profile proxy</strong><p>Facebook và Messenger dùng proxy đã gán. Outlook và các website khác dùng kết nối Internet trực tiếp.</p></div></div>}
  </>
 }
 function settingsPanel(){
  const section=settingsSection
  if(section==='bm')return <BMProfilesPage notify={notify} refresh={()=>void refresh()} act={act} edit={profile=>{setEdit(profile);setModal('profile')}} logs={profile=>void showLogs(profile)}/>
  if(section==='proxies')return <><div className="settings-section-heading"><div><h2>Proxy Manager</h2><p>Nhập, kiểm tra và quản lý các kết nối của Chrome profile.</p></div><button className="button primary" onClick={()=>setModal('import')}><Upload size={15}/>Import proxies</button></div><ProxyPage act={(path,data,method)=>void act(path,data,method)} importProxies={()=>setModal('import')}/></>
  if(section==='automation')return <><div className="settings-section-heading"><div><h2>Automation Tasks</h2><p>Các tác vụ đã lưu và tiến trình theo từng profile.</p></div><button className="button primary" onClick={()=>setModal('builder')}><Plus size={15}/>Create task</button></div>{taskData.error&&<p className="warning" role="alert">{taskData.error}</p>}{taskData.total?<><div className="tasks-grid">{visibleTasks.map(task=><section className="panel task-card" key={task.id}><div className="task-card-top"><div className="stat-icon purple"><Workflow size={20}/></div><Badge status={task.status}/></div><h2>{task.name}</h2><p>{task.actions.length} actions · {task.runs} profile runs</p><div className="task-sequence">{task.actions.map((action,index)=><span key={index}>{action.replaceAll('_',' ')}</span>)}</div><div className="task-card-actions"><button className="button primary" onClick={()=>run(listedSelected.size?[...listedSelected]:[],task.id)}><Play size={14}/>Run task</button><button className="button" disabled={!task.active_runs} onClick={()=>void act('/tasks/'+task.id+'/stop')}><Square size={13}/>Stop</button><button className="text-button" onClick={()=>setSettingsSection('history')}>History<ArrowUpRight size={14}/></button></div></section>)}</div><Pagination total={taskData.total} page={currentTaskPage} pageSize={taskPageSize} label="tasks" onPageChange={setTaskPage} onPageSizeChange={size=>{setTaskPageSize(size);setTaskPage(1)}}/></>:<section className="panel"><Empty title="Chưa có tác vụ" detail="Tạo và lưu quy trình để dùng lại trên các Chrome profile."><button className="button primary" onClick={()=>setModal('builder')}><Plus size={16}/>Create task</button></Empty></section>}</>
  if(section==='history')return <HistoryPage notify={notify}/>
  if(section==='recipient')return <PageRecipientPage notify={notify}/>
  if(section==='app')return <SettingsPage notify={notify} refresh={()=>void refresh()}/>
  return <><div className="settings-section-heading"><div><h2>Dữ liệu & công cụ</h2><p>Nhập dữ liệu và mở các công cụ cho Chrome profile.</p></div></div><div className="settings-data-grid">
   <section className="panel settings-data-card"><Globe size={23}/><h3>Chrome profile</h3><p>Tạo phiên Chrome mới; chọn và quản lý các profile trong Dashboard.</p><button className="button" onClick={create}><Plus size={15}/>New profile</button><button className="button" disabled={!!busy} onClick={()=>void act('/profiles/bulk-create',{count:100})}><Layers size={15}/>Create 100 Profiles</button></section>
   <section className="panel settings-data-card"><Upload size={23}/><h3>Nhập proxy</h3><p>Nhập danh sách kết nối và gán cho các profile phù hợp.</p><button className="button" onClick={()=>setModal('import')}><Upload size={15}/>Import Proxies</button></section>
   <section className="panel settings-data-card"><Mail size={23}/><h3>Outlook</h3><p>Đăng nhập và nhận mã bằng đúng Chrome profile đã chọn.</p><button className="button" onClick={()=>{setOutlookId(undefined);setModal('outlook')}}><Mail size={15}/>Outlook · Đăng nhập & lấy mã</button></section>
   <section className="panel settings-data-card"><ShieldCheck size={23}/><h3>Authenticator</h3><p>Thiết lập tiện ích cho các Chrome profile đang chọn.</p><button className="button" onClick={()=>setModal('authenticator')}><ShieldCheck size={15}/>Authenticator</button></section>
  </div><p className="hint">Đang chọn {listedSelected.size} profile. <button className="text-button" onClick={()=>setPage('Dashboard')}>Mở Dashboard để đổi lựa chọn<ArrowUpRight size={13}/></button></p><FacebookOverview selected={listedSelected} timezone={facebookTimezone} setTimezone={changeFacebookTimezone} check={checkFacebook} busy={!!busy}/></>
 }
 return <div className={'app-shell '+(compact?'sidebar-collapsed':'')}>
  <aside className="sidebar"><div className="brand"><div className="brand-mark"><Globe size={21}/></div><div>Chrome Manager<span>Browser workspace</span></div></div><ProductSwitcher/>
   <div className="nav-label">WORKSPACE</div><nav aria-label="Điều hướng chính">{navigation.map(item=><button key={item.name} aria-label={item.name} title={item.name} className={page===item.name?'active':''} onClick={()=>setPage(item.name)}><item.icon size={18}/><span>{item.name}</span></button>)}</nav>
   <div className="sidebar-bottom"><button className={'settings-gear '+(page==='Settings'?'active':'')} aria-label="Settings" title="Settings" onClick={()=>setPage('Settings')}><Settings size={18}/><span>Settings</span></button><IdentityAccount/></div>
  </aside>
  <div className="main-shell"><header className="topbar"><div className="topbar-left"><button className="icon-button sidebar-toggle" aria-label={compact?'Expand sidebar':'Collapse sidebar'} title={compact?'Expand sidebar':'Collapse sidebar'} onClick={toggleSidebar}>{compact?<PanelLeftOpen size={18}/>:<PanelLeftClose size={18}/>}</button><div className="breadcrumb">Local workspace <span>/</span><strong>{page}</strong></div></div><div className="topbar-right"><span className={'connection '+(connected?'online':'')}><i/>{connected?'Live connection':'Reconnecting…'}</span><button className="header-help" onClick={()=>setModal('help')}><CircleHelp size={15}/><span>Quick start</span></button><button className="icon-button" title="Refresh workspace" disabled={!!busy} onClick={()=>void refresh()}><RefreshCw size={16}/></button></div></header>
   <main>{page!=='Dashboard'&&<div className="page-heading"><div><h1>{page}</h1><p>{subtitles[page]}</p></div>{page==='Lịch đăng Page'&&<button type="button" className="button small" onClick={()=>setPage('Lịch đã lưu')}><Film size={14}/>Xem lịch đã lưu</button>}{page==='Lịch đã lưu'&&<button type="button" className="button small" onClick={()=>setPage('Lịch đăng Page')}><CalendarClock size={14}/>Tạo lịch mới</button>}</div>}
    {error&&<div className="warning"><AlertCircle size={17}/>{error} Start the backend using start.bat.</div>}
    {system?.resource_warning&&<div className="warning"><MemoryStick size={17}/>{system.resource_warning}</div>}
    {page==='Dashboard'&&profilePanel()}
    {page==='Pages'&&<ChromePages timezone={facebookTimezone} setTimezone={changeFacebookTimezone} openFacebook={profile=>setFacebookId(profile.id)} openProfiles={()=>setPage('Dashboard')}/>}
    {(page==='Lịch đăng Page'||page==='Lịch đã lưu')&&<ReelSchedulerPage initialProfileId={listedSelected.values().next().value} notify={notify} view={page==='Lịch đã lưu'?'saved':'create'}/>}
    {page==='Settings'&&<WorkspaceSettings section={settingsSection} onSection={setSettingsSection}>{settingsPanel()}</WorkspaceSettings>}
    <footer><span><ShieldCheck size={13}/>Dữ liệu được lưu trên máy này.</span><span>Chrome Multi-Profile Manager</span></footer>
   </main>
  </div>
  {toast&&<div className={'toast '+(toast.error?'error':'')} role="status">{toast.error?<AlertCircle size={19}/>:<CheckCircle2 size={19}/>}<span>{toast.text}</span><button className="icon-button" aria-label="Dismiss notification" onClick={()=>setToast(null)}><X size={16}/></button></div>}
  {facebookProfile&&<FacebookDialog profile={facebookView(facebookProfile)} timezone={facebookTimezone} check={checkFacebook} stop={id=>void act('/facebook/'+id+'/stop')} close={()=>setFacebookId(null)}/>}
  {modal==='outlook'&&<OutlookDialog initialProfileId={outlookId??listedSelected.values().next().value} notify={notify} refresh={()=>void refresh()} close={()=>setModal(null)}/>}
  {modal==='authenticator'&&<AuthenticatorDialog selected={listedSelected} notify={notify} close={()=>setModal(null)} refresh={()=>void refresh()}/>}
  {modal==='profile'&&<ProfileForm profile={edit} notify={notify} done={completed} close={()=>setModal(null)}/>}
  {modal==='import'&&<ImportForm notify={notify} done={completed} close={()=>setModal(null)}/>}
  {modal==='run'&&<RunForm initial={runIds} taskId={chosenTask} notify={notify} done={completed} close={()=>setModal(null)}/>}
  {modal==='builder'&&<TaskBuilder notify={notify} done={completed} close={()=>setModal(null)}/>}
  {deleting&&<Modal title={'Delete '+deleting.name+'?'} subtitle="This permanently deletes the profile folder, cookies and saved sessions. Task history is kept." close={()=>setDeleting(null)}><div className="modal-actions"><button className="button" onClick={()=>setDeleting(null)}>Cancel</button><button className="button danger" onClick={async()=>{const deleted=await act('/profiles/'+deleting.id+'?confirm=true',undefined,'DELETE');if(!deleted)return;setSelected(old=>{const next=new Set(old);next.delete(deleting.id);return next});setDeleting(null)}}>Delete profile and data</button></div></Modal>}
  {profileLogs&&<ActivityLogDialog key={profileLogs.profile.id} profile={profileLogs.profile} logs={profileLogs.logs} close={()=>setProfileLogs(null)}/>}
  {modal==='help'&&<Modal title="Thiết lập không gian làm việc" close={()=>setModal(null)}><ol className="quick-start"><li>Mở Dashboard để tạo, chọn và quản lý Chrome profile.</li><li>Nhập proxy trực tiếp hoặc mở Proxy Manager trong Settings.</li><li>Chọn profile rồi mở Chrome hoặc kiểm tra Facebook khi cần.</li><li>Xem Pages đã lưu và tạo lịch trong Lịch đăng Page.</li><li>Quản lý tác vụ, lịch sử và giới hạn tài nguyên trong Settings.</li></ol><p className="hint">Dừng Chrome trước khi đóng ứng dụng để giữ phiên và giải phóng tài nguyên.</p></Modal>}
 </div>
}
