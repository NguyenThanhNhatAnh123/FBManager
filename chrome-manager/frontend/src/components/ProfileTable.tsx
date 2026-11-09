import { useEffect, useState } from 'react'
import { Search, Play, Square, Pencil, Trash2, Cable, ListFilter, FileText, Globe, Plus, ContactRound as Facebook, Mail } from 'lucide-react'
import { Empty } from './ui'
import { Pagination } from './Pagination'
import { FacebookStatus } from './Facebook'
import { facebookActive } from '../services/facebook'
import type { Profile } from '../services/types'
import { useServerPage } from '../hooks/useServerPage'

interface Props {
  proxyOnly?:boolean;timezone?:string;selected:Set<number>;setSelected:(s:Set<number>)=>void
  act:(path:string,data?:unknown)=>void;edit:(p:Profile)=>void;remove:(p:Profile)=>void
  run:(ids:number[])=>void;logs:(p:Profile)=>void;create:()=>void
  facebook:(p:Profile)=>void
  outlook:(p:Profile)=>void
}

export function ProfileTable({proxyOnly=false,timezone,selected,setSelected,act,edit,remove,run,logs,create,facebook,outlook}:Props){
  const [search,setSearch]=useState(''),[filter,setFilter]=useState('All statuses'),[postFilter,setPostFilter]=useState('all')
  const [page,setPage]=useState(1),[pageSize,setPageSize]=useState(10)
  const data=useServerPage<Profile>('profiles',{scope:proxyOnly?'proxy':'regular',q:search.trim(),status:filter,post_status:postFilter,zone:timezone,preview:3},page,pageSize,setPage)
  const visible=data.items,safePage=page,totalProfiles=Number(data.summary?.all||0)
  const all=visible.length>0&&visible.every(p=>selected.has(p.id))
  useEffect(()=>setPage(1),[search,filter,postFilter,pageSize])
  function toggle(id:number){const n=new Set(selected);n.has(id)?n.delete(id):n.add(id);setSelected(n)}
  return <section className="panel profiles-panel">
    <div className="panel-title"><div><h2>Chrome profiles <span className="count">{totalProfiles}</span></h2><p>{proxyOnly?'Only profiles with an assigned proxy are shown.':'An isolated browser session for every workflow.'}</p></div><button className="button small" onClick={create}><Plus size={15}/>New profile</button></div>
    <div className="table-tools facebook-table-tools"><div className="profile-tabs">{[
      {value:'All statuses',label:'All profiles',count:totalProfiles},
      {value:'Running',label:'Running',count:Number(data.summary?.running||0)},
      {value:'Stopped',label:'Stopped',count:Number(data.summary?.stopped||0)},
    ].map(tab=><button key={tab.value} className={filter===tab.value?'active':''} aria-pressed={filter===tab.value} onClick={()=>setFilter(tab.value)}>{tab.label}<span>{tab.count}</span></button>)}</div>
      <div className="table-filters"><div className="search"><Search size={14}/><input aria-label="Search profiles" placeholder="Tìm profile, tên FB, Page, proxy…" value={search} onChange={e=>setSearch(e.target.value)}/></div>
        <select aria-label="Lọc bài đăng hôm nay" value={postFilter} onChange={e=>setPostFilter(e.target.value)}><option value="all">Tất cả Page</option><option value="posted">Đã đăng hôm nay</option><option value="not_seen">Chưa thấy bài hôm nay</option><option value="unchecked">Cần kiểm tra</option></select>
        <div className="filter-select"><ListFilter size={14}/><select aria-label="Filter browser status" value={filter} onChange={e=>setFilter(e.target.value)}>{['All statuses','Running','Stopped','Queued','Starting','Error'].map(v=><option key={v}>{v}</option>)}</select></div>
      </div>
    </div>
    {data.error&&<p className="warning" role="alert">{data.error}</p>}
    {data.loading&&!visible.length?<p className="loading">Đang tải profile…</p>:totalProfiles===0?<Empty title={proxyOnly?'No profiles with a proxy yet':'A clean slate for your next workflow'} detail={proxyOnly?'Import proxies or assign one in Chrome Profiles to show them here.':'Create your first profile or set up 100 at once.'}>{!proxyOnly&&<button className="button" onClick={create}><Plus size={16}/>Create a profile</button>}</Empty>:
    <div className="table-scroll"><table className="profile-table facebook-profile-table"><thead><tr>
      <th className="check-cell"><input aria-label="Select current page" type="checkbox" checked={all} onChange={()=>{const n=new Set(selected);visible.forEach(p=>all?n.delete(p.id):n.add(p.id));setSelected(n)}}/></th>
      <th>ID</th><th>Profile name</th><th>Tên Facebook</th><th>Page quản lý · Bài hôm nay</th><th>Proxy / IP address</th><th>Outlook</th><th className="actions-heading">Actions</th>
    </tr></thead><tbody>
    {visible.map(p=>{
      const active=facebookActive(p),outlookActive=!!p.outlook?.active,browserBusy=['Running','Queued','Starting'].includes(p.browser_status)
      const outlookState=p.outlook
      return <tr key={p.id} className={selected.has(p.id)?'selected':''}>
        <td className="check-cell"><input aria-label={'Select '+p.name} type="checkbox" checked={selected.has(p.id)} onChange={()=>toggle(p.id)}/></td>
        <td className="mono muted">#{String(p.id).padStart(3,'0')}</td>
        <td><div className="profile-name"><span className={'browser-avatar '+(p.browser_status==='Running'?'active':'')}><Globe size={17}/></span><div><strong>{p.name}</strong></div></div></td>
        <td className="facebook-account-cell">{p.facebook?.name?<button className="facebook-account-name" onClick={()=>facebook(p)}><Facebook size={14}/>{p.facebook.name}</button>:<span className="muted">—</span>}</td>
        <td className="facebook-pages-cell">{p.facebook?.pages.length?<div className="facebook-page-list">{p.facebook.pages.map(pg=><div className="facebook-page-row" key={pg.id}><button title={pg.name} className="facebook-page-name" onClick={()=>facebook(p)}>{pg.name}</button><FacebookStatus page={pg}/></div>)}<button className="text-button facebook-page-details" onClick={()=>facebook(p)}>{(p.facebook.page_count||p.facebook.pages.length)>3?`+${(p.facebook.page_count||p.facebook.pages.length)-3} Page khác · `:''}Xem chi tiết ({p.facebook.page_count||p.facebook.pages.length} Page)</button></div>:<span className="muted">{active?'Đang đọc danh sách Page…':'Chưa có dữ liệu Page'}</span>}</td>
        <td>{p.proxy?<div className="proxy-cell"><span className="mono">{p.proxy.host}:{p.proxy.port}</span><small>{p.proxy.protocol.toUpperCase()}{p.proxy.has_credentials?' · Authenticated':''}</small><small className="mono facebook-proxy-ip">{p.proxy.ip_address||'Chưa kiểm tra IP'}</small></div>:<span className="muted">Not assigned</span>}</td>
        <td className="outlook-status-cell">{outlookState?.email?<button className="outlook-status-button outlook-email" aria-label={'Outlook của '+p.name+': '+outlookState.email} title={'Mở Outlook của '+outlookState.email} onClick={()=>outlook(p)}>{outlookState.email}</button>:<span className="muted">—</span>}</td>
        <td><div className="row-actions"><button className={'row-open '+(browserBusy||active||outlookActive?'stop':'')} title={browserBusy||active||outlookActive?'Stop Chrome':'Open Chrome'} onClick={()=>act('/profiles/'+p.id+(browserBusy||active||outlookActive?'/stop':'/start'))}>{browserBusy||active||outlookActive?<Square size={12}/>:<Play size={12}/>}<span>{browserBusy||active||outlookActive?'Stop':'Open'}</span></button><button className="icon-button" title="Edit profile" disabled={active||outlookActive} onClick={()=>edit(p)}><Pencil size={14}/></button><button className="icon-button" title="Outlook đăng nhập và lấy mã" onClick={()=>outlook(p)}><Mail size={14}/></button><button className="icon-button" title="Check proxy" disabled={!p.proxy_id} onClick={()=>act('/proxies/check',{proxy_ids:[p.proxy_id]})}><Cable size={14}/></button><button className="icon-button" disabled={p.browser_mode==='manual'||active||outlookActive} title="Run task" onClick={()=>run([p.id])}><Play size={14}/></button><button className="icon-button" title="Profile logs" onClick={()=>logs(p)}><FileText size={14}/></button><button className="icon-button danger-text" title="Delete profile" disabled={active||outlookActive} onClick={()=>remove(p)}><Trash2 size={14}/></button></div></td>
      </tr>
    })}
    {!data.total&&<tr><td colSpan={8} className="no-matches">No profiles match your search.</td></tr>}
    </tbody></table></div>}
    <Pagination total={data.total} page={safePage} pageSize={pageSize} label="profiles" selected={selected.size} onPageChange={setPage} onPageSizeChange={size=>{setPageSize(size);setPage(1)}}/>
  </section>
}
