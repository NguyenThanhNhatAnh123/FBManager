import { useEffect, useState } from 'react'
import { ArrowUpRight, Building2, Clock3, Globe, Layers, Search, Settings } from 'lucide-react'
import type { FacebookPage, Profile } from '../services/types'
import { FacebookStatus } from '../components/Facebook'
import { Empty, Modal, time } from '../components/ui'
import { Pagination } from '../components/Pagination'
import { useServerPage } from '../hooks/useServerPage'
import { facebookDay } from '../services/facebook'

interface PageSource {profile:Profile;page:FacebookPage;fresh:boolean}
interface CachedPage {key:string;name:string;url:string|null;sources:PageSource[];display:FacebookPage;source_count?:number}

function identity(value:string,fallback:string){
 try{
  const parsed=new URL(value)
  if(parsed.protocol!=='https:'||!['facebook.com','www.facebook.com','m.facebook.com'].includes(parsed.hostname)||parsed.username||parsed.password||parsed.port&&parsed.port!=='443')return {key:fallback,url:null}
  const path=parsed.pathname.replace(/^\/|\/$/g,'')
  const id=path==='profile.php'?parsed.searchParams.get('id'):/^[0-9]+$/.test(path)?path:/^(?:people|pages)\/[^/]+\/([0-9]+)$/.exec(path)?.[1]
  if(id&&/^[0-9]+$/.test(id))return {key:'id:'+id,url:'https://www.facebook.com/profile.php?id='+id}
  if(/^[A-Za-z0-9._-]+$/.test(path)&&!['me','reel','reels','settings','login','checkpoint','profile.php'].includes(path.toLowerCase()))return {key:'name:'+path.toLowerCase(),url:'https://www.facebook.com/'+path}
 }catch{/* Keep an unrecognized cached row attached to its own source. */}
 return {key:fallback,url:null}
}

export function cachedChromePages(profiles:Profile[],timezone:string):CachedPage[]{
 const today=facebookDay(timezone).check_date,found=new Map<string,CachedPage>()
 for(const profile of profiles)for(const page of profile.facebook?.pages||[]){
  const canonical=identity(page.url,'profile:'+profile.id+':page:'+page.id)
  const fresh=page.timezone===timezone&&page.check_date===today
  const source={profile,page:fresh?page:{...page,status:'unchecked',posts_today:null,posts:[]},fresh}
  const existing=found.get(canonical.key)
  if(existing){
   const previous=existing.sources.findIndex(value=>value.profile.id===profile.id)
   if(previous<0)existing.sources.push(source)
   else if(source.fresh&&!existing.sources[previous].fresh||source.fresh===existing.sources[previous].fresh&&(source.page.checked_at||'')>(existing.sources[previous].page.checked_at||''))existing.sources[previous]=source
   continue
  }
  found.set(canonical.key,{key:canonical.key,name:page.name,url:canonical.url,sources:[source],display:source.page})
 }
 return [...found.values()].map(row=>{
  const current=row.sources.filter(source=>source.fresh),ordered=[...row.sources].sort((a,b)=>(b.page.checked_at||'').localeCompare(a.page.checked_at||''))
  const chosen=ordered.find(source=>source.fresh)||ordered[0]
  const posted=current.some(source=>source.page.status==='posted')
  const status=posted?'posted':current.some(source=>source.page.status==='checking')?'checking':current.some(source=>source.page.status==='unknown')?'unknown':current.length&&current.every(source=>source.page.status==='not_seen')?'not_seen':'unchecked'
  const posts=[...new Map(current.flatMap(source=>source.page.posts).map(post=>[post.url,post])).values()]
  const count=posted?Math.max(posts.length,...current.map(source=>source.page.posts_today||0),1):status==='not_seen'?0:null
  return {...row,name:chosen.page.name,display:{...chosen.page,status,posts_today:count,posts}}
 }).sort((a,b)=>a.name.localeCompare(b.name))
}

export function ChromePages({timezone,setTimezone,openFacebook,openProfiles}:{timezone:string;setTimezone:(value:string)=>void;openFacebook:(profile:Profile)=>void;openProfiles:()=>void}){
 const [search,setSearch]=useState(''),[status,setStatus]=useState('all'),[page,setPage]=useState(1),[pageSize,setPageSize]=useState(25)
 const [sourceRow,setSourceRow]=useState<CachedPage|null>(null),[sourcePage,setSourcePage]=useState(1),[sourceSize,setSourceSize]=useState(10)
 const sourceData=useServerPage<Profile>('page-sources',{key:sourceRow?.key},sourcePage,sourceSize,setSourcePage,!!sourceRow)
 const data=useServerPage<CachedPage>('pages',{q:search.trim(),status,zone:timezone},page,pageSize,setPage)
 const catalog=data.items,rows=catalog,currentPage=page,visible=rows
 const zones=[...new Set([timezone,'America/Los_Angeles','Asia/Ho_Chi_Minh','America/New_York','UTC'])]
 useEffect(()=>setPage(1),[search,status,timezone,pageSize])
 return <div className="chrome-pages">
  <div className="chrome-pages-summary">
   <div><Layers size={18}/><span><strong>{Number(data.summary?.all||0)}</strong> Page đã lưu</span></div>
   <div><Globe size={18}/><span><strong>{Number(data.summary?.profiles||0)}</strong> profile nguồn</span></div>
   <div><Clock3 size={18}/><span><strong>{Number(data.summary?.posted||0)}</strong> Page có bài hôm nay</span></div>
  </div>
  <section className="panel">
   <div className="panel-title"><div><h2>Pages <span className="count">{Number(data.summary?.all||0)}</span></h2><p>Danh sách từ các phiên Chrome đã kiểm tra Facebook.</p></div><button className="button small" onClick={openProfiles}><Settings size={14}/>Quản lý nguồn</button></div>
   <div className="chrome-pages-tools table-tools">
    <div className="search"><Search size={15}/><input aria-label="Tìm Page hoặc profile nguồn" placeholder="Tìm Page, Facebook hoặc profile…" value={search} onChange={event=>setSearch(event.target.value)}/></div>
    <select aria-label="Trạng thái Page đã lưu" value={status} onChange={event=>setStatus(event.target.value)}><option value="all">Tất cả trạng thái</option><option value="posted">Đã có bài hôm nay</option><option value="not_seen">Chưa thấy bài hôm nay</option><option value="unchecked">Cần kiểm tra</option><option value="unknown">Chưa xác định</option></select>
    <label>Múi giờ<select aria-label="Múi giờ danh sách Page" value={timezone} onChange={event=>setTimezone(event.target.value)}>{zones.map(zone=><option key={zone}>{zone}</option>)}</select></label>
   </div>
   {data.error&&<p className="warning" role="alert">{data.error}</p>}
   <p className="chrome-pages-cache-note">Kết quả đã lưu · {facebookDay(timezone).check_date} · Mở trang này không bắt đầu kiểm tra Facebook. Số bài là mức tối thiểu đã quan sát được.</p>
   {!data.summary?.all?<Empty title="Chưa có Page đã lưu" detail="Mở Dashboard để kiểm tra Facebook theo từng profile, hoặc kiểm tra hàng loạt tại Settings → Dữ liệu & công cụ."><button className="button" onClick={openProfiles}><Settings size={15}/>Mở Chrome Profiles</button></Empty>:!data.total?<Empty title="Không có Page phù hợp" detail="Thử đổi từ khóa hoặc bộ lọc trạng thái."/>:<>
    <div className="table-scroll"><table className="chrome-pages-table"><thead><tr><th>Page</th><th>Profile nguồn</th><th>Bài hôm nay</th><th>Cập nhật gần nhất</th><th>Chi tiết</th></tr></thead><tbody>{visible.map(row=><tr key={row.key}>
     <td><div className="chrome-page-name">{row.url?<a href={row.url} target="_blank" rel="noreferrer">{row.name}<ArrowUpRight size={13}/></a>:<strong>{row.name}</strong>}<small>{row.url||'Chưa có link Page hợp lệ'}</small></div></td>
     <td><div className="chrome-page-sources">{row.sources.map(source=><button className="chrome-page-source" key={source.profile.id} onClick={()=>openFacebook(source.profile)} title={'Dữ liệu đã lưu của '+source.profile.name}>{source.profile.bm?<Building2 size={13}/>:<Globe size={13}/>}<span>{source.profile.name}<small>{source.profile.facebook?.name||'Chưa đọc tên Facebook'}</small></span></button>)}{(row.source_count||0)>row.sources.length&&<button className="text-button" onClick={()=>{setSourceRow(row);setSourcePage(1)}}>Xem tất cả {row.source_count} profile nguồn</button>}</div></td>
     <td><FacebookStatus page={row.display}/></td>
     <td><span>{time(row.display.checked_at)}</span><small className="muted">{row.display.check_date||'Chưa kiểm tra'}{row.display.timezone?' · '+row.display.timezone:''}</small></td>
     <td><button className="text-button" onClick={()=>openFacebook(row.sources[0].profile)}>Xem dữ liệu<ArrowUpRight size={13}/></button></td>
    </tr>)}</tbody></table></div>
    <Pagination total={data.total} page={currentPage} pageSize={pageSize} label="Pages" onPageChange={setPage} onPageSizeChange={setPageSize}/>
   </>}
  </section>
  {sourceRow&&<Modal title={'Profile nguồn · '+sourceRow.name} close={()=>setSourceRow(null)}>{sourceData.error&&<p className="warning" role="alert">{sourceData.error}</p>}<div className="chrome-page-sources">{sourceData.items.map(profile=><button className="chrome-page-source" key={profile.id} onClick={()=>{setSourceRow(null);openFacebook(profile)}}><Globe size={13}/><span>{profile.name}<small>{profile.facebook?.name||'Chưa đọc tên Facebook'}</small></span></button>)}</div><Pagination total={sourceData.total} page={sourcePage} pageSize={sourceSize} label="profile nguồn của Page" onPageChange={setSourcePage} onPageSizeChange={size=>{setSourceSize(size);setSourcePage(1)}}/></Modal>}
 </div>
}
