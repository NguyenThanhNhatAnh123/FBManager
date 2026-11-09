import { useEffect, useState } from 'react'
import { Eye, Search } from 'lucide-react'
import type { Run, Notify } from '../services/types'
import { Badge, Empty, Modal, Progress, time } from '../components/ui'
import { Pagination } from '../components/Pagination'
import { useServerPage, useServerRecord } from '../hooks/useServerPage'

function screenshotUrl(value:unknown){
 const match=/^\/api\/(?:v1\/)?screenshots\/(run_\d+_step_\d+\.png)$/.exec(String(value||''))
 return match?'/api/v1/screenshots/'+match[1]:null
}

export function HistoryPage({notify:_notify}:{notify:Notify}){
 const [page,setPage]=useState(1),[pageSize,setPageSize]=useState(10)
 const [status,setStatus]=useState('All'),[search,setSearch]=useState('')
 const query=search.trim()
 const [detail,setDetail]=useState<Run|null>(null)
 useEffect(()=>setPage(1),[status,query,pageSize])
 const data=useServerPage<Run>('task-history',{status:status==='All'?'all':status,q:query},page,pageSize,setPage)
 const loading=data.loading
 const detailData=useServerRecord<Run>(detail?'task-runs/'+detail.id:null)
 const selected=detailData.value
 return <><section className="panel"><div className="panel-title"><div><h2>Task history <span className="count">{data.total}</span></h2><p>Every run, with saved results and a clear activity trail.</p></div></div>
 <div className="table-tools"><div className="search"><Search size={15}/><input aria-label="Search task history" placeholder="Search profile or run ID…" value={search} onChange={e=>setSearch(e.target.value)}/></div><select aria-label="Filter run status" value={status} onChange={e=>setStatus(e.target.value)}>{['All','Pending','Running','Completed','Failed','Cancelled'].map(s=><option key={s} value={s}>{s==='All'?'All statuses':s}</option>)}</select></div>
 {data.error&&<p className="warning" role="alert">{data.error}</p>}
 {loading&&data.items.length===0?<div className="loading">Loading task history…</div>:!data.total?<Empty title={query||status!=='All'?'No matching runs':'No runs yet'} detail={query||status!=='All'?'Try another search or status filter.':'Run a saved task on one or more profiles to see its results here.'}/>:<div className="table-scroll"><table><thead><tr><th>Run</th><th>Profile</th><th>Task</th><th>Status</th><th>Progress</th><th>Started</th><th>Results</th></tr></thead><tbody>{data.items.map(r=><tr key={r.id}><td className="mono">#{r.id}</td><td>{r.profile_name}</td><td>Task #{r.task_id}</td><td><Badge status={r.status}/></td><td><Progress value={r.progress}/></td><td className="muted">{time(r.started_at)}</td><td><button className="button small" onClick={()=>setDetail(r)}><Eye size={14}/>View</button></td></tr>)}</tbody></table></div>}
 <Pagination total={data.total} page={page} pageSize={pageSize} label="runs" onPageChange={setPage} onPageSizeChange={size=>{setPageSize(size);setPage(1)}}/>
 </section>
 {detail&&<Modal wide title={'Run #'+detail.id+' · '+detail.profile_name} subtitle={time(detail.started_at)} close={()=>setDetail(null)}><div className="form-stack">{detailData.loading?<p role="status">Loading run results…</p>:detailData.error?<p className="warning" role="alert">{detailData.error}</p>:selected&&<><Badge status={selected.status}/>{selected.error_message&&<div className="warning">{selected.error_message}</div>}{selected.result.map((result,index)=>{const image=screenshotUrl(result.screenshot);return <div className="result-step" key={index}><h3>Step {String(result.step)} · {String(result.action).replaceAll('_',' ')}</h3>{image?<a href={image} target="_blank" rel="noreferrer"><img alt={'Screenshot from step '+String(result.step)} src={image} className="screenshot"/></a>:<dl className="result-fields">{Object.entries(result).filter(([key])=>!['step','action'].includes(key)).map(([key,value])=><div key={key}><dt>{key.replaceAll('_',' ')}</dt><dd>{typeof value==='object'?JSON.stringify(value):String(value??'—')}</dd></div>)}</dl>}</div>})}{!selected.result.length&&<p className="muted">No steps have completed.</p>}</>}</div></Modal>}
 </>
}
