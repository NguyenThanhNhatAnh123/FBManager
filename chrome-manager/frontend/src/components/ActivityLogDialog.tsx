import { useState } from 'react'
import { Search } from 'lucide-react'
import type { Log, Profile } from '../services/types'
import { Modal, time } from './ui'
import { Pagination } from './Pagination'
import { useServerPage } from '../hooks/useServerPage'

export function ActivityLogDialog({profile,logs:_loaded,close}:{profile:Profile;logs:Log[];close:()=>void}){
 const [search,setSearch]=useState(''),[level,setLevel]=useState('all'),[page,setPage]=useState(1),[pageSize,setPageSize]=useState(25)
 const data=useServerPage<Log>('logs',{profile_id:profile.id,q:search.trim(),level},page,pageSize,setPage)
 const rows=data.items,current=page,visible=rows,levels=(data.summary?.levels||[]) as string[]
 return <Modal wide title={profile.name+' · Activity'} subtitle={data.total+' matching events'} close={close}>
  <div className="table-tools activity-log-filters"><div className="search"><Search size={14}/><input aria-label="Search activity" placeholder="Search message, level or event ID…" value={search} onChange={event=>{setSearch(event.target.value);setPage(1)}}/></div><select aria-label="Filter activity level" value={level} onChange={event=>{setLevel(event.target.value);setPage(1)}}><option value="all">All levels</option>{levels.map(value=><option value={value} key={value}>{value}</option>)}</select></div>
  {data.error&&<p className="warning" role="alert">{data.error}</p>}
  <div className="log-view" aria-label="Profile activity events">{visible.map(log=><div key={log.id} className={log.level==='error'?'error-text':''}><span>{time(log.created_at)}</span> {log.message}</div>)}{!rows.length&&<p>{search||level!=='all'?'No activity matches these filters.':'No activity recorded.'}</p>}</div>
  <Pagination total={data.total} page={current} pageSize={pageSize} label="events" onPageChange={setPage} onPageSizeChange={size=>{setPageSize(size);setPage(1)}}/>
 </Modal>
}
