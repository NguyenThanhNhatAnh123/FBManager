import { useEffect, useState } from 'react'
import { RefreshCw, Search, Trash2, Upload } from 'lucide-react'
import type { ProxyItem } from '../services/types'
import { Badge, Empty, time } from '../components/ui'
import { Pagination } from '../components/Pagination'
import { useServerPage } from '../hooks/useServerPage'
interface Props{act:(path:string,data?:unknown,method?:string)=>void;importProxies:()=>void}
export function ProxyPage({act,importProxies}:Props){
 const [search,setSearch]=useState(''),[status,setStatus]=useState('All statuses')
 const [page,setPage]=useState(1),[pageSize,setPageSize]=useState(10)
 const data=useServerPage<ProxyItem>('proxies',{q:search.trim(),status},page,pageSize,setPage)
 const current=page,visible=data.items,total=Number(data.summary?.all||0)
 useEffect(()=>setPage(1),[search,status,pageSize])
 return <section className="panel"><div className="panel-title"><div><h2>Proxy inventory <span className="count">{total}</span></h2><p>Every connection, in one place. Credentials stay encrypted.</p></div><button className="button" disabled={!total} onClick={()=>act('/proxies/check',{})}><RefreshCw size={15}/>Check all proxies</button></div>
 {data.error&&<p className="warning" role="alert">{data.error}</p>}
 <div className="table-tools"><div className="search"><Search size={15}/><input aria-label="Search proxies" placeholder="Search endpoint, profile or IP…" value={search} onChange={e=>setSearch(e.target.value)}/></div><select aria-label="Filter proxy status" value={status} onChange={e=>setStatus(e.target.value)}>{['All statuses','Working','Failed','Timeout','Unchecked','Checking'].map(s=><option key={s}>{s}</option>)}</select></div>
 {!total?<Empty title="Connect your proxies" detail="Upload a TXT or CSV file. Each proxy is assigned to its own browser profile."><button className="button" onClick={importProxies}><Upload size={16}/>Import proxies</button></Empty>:<div className="table-scroll"><table><thead><tr><th>ID</th><th>Endpoint</th><th>Profile</th><th>Public IP</th><th>Latency</th><th>Status</th><th>Last check</th><th>Actions</th></tr></thead><tbody>{visible.map(p=><tr key={p.id}><td className="mono">#{p.id}</td><td><strong className="mono">{p.host}:{p.port}</strong><small>{p.protocol.toUpperCase()}{p.has_credentials?' · Authenticated':''}</small></td><td>{p.profile_name||'Unassigned'}</td><td className="mono ip-value">{p.ip_address||'—'}</td><td>{p.latency_ms!==null?p.latency_ms+' ms':'—'}</td><td title={p.error_message||''}><Badge status={p.checking?'Checking':p.status}/>{p.error_message&&!p.checking&&<small className="proxy-status-error">{p.error_message.replace(/^\[[A-Z0-9_]+\]\s*/, '')}</small>}</td><td className="muted">{time(p.last_checked)}</td><td><div className="row-actions"><button className="button small" disabled={p.checking} onClick={()=>act('/proxies/check',{proxy_ids:[p.id]})}>Check</button><button className="icon-button danger-text" title={p.profile_name?'Unassign proxy before deletion':'Delete proxy'} disabled={!!p.profile_name} onClick={()=>act('/proxies/'+p.id,undefined,'DELETE')}><Trash2 size={15}/></button></div></td></tr>)}{!visible.length&&<tr><td colSpan={8} className="no-matches">No proxies match these filters.</td></tr>}</tbody></table></div>}
 <Pagination total={data.total} page={current} pageSize={pageSize} label="proxies" onPageChange={setPage} onPageSizeChange={size=>{setPageSize(size);setPage(1)}}/>
 </section>
}
