import { useEffect, useState } from 'react'
import { ContactRound as Facebook, RefreshCw, ExternalLink, Clock3, CheckCircle2, CircleHelp, Search, Square } from 'lucide-react'
import type { FacebookPage, Profile } from '../services/types'
import { Modal, time } from './ui'
import { accountLabels, facebookActive, facebookDay } from '../services/facebook'
import { Pagination } from './Pagination'
import { useServerPage, useServerRecord } from '../hooks/useServerPage'

export function FacebookStatus({page}:{page:FacebookPage}){
 const label=page.status==='posted'?`Đã đăng · ≥${page.posts_today||1} bài`:page.status==='not_seen'?'Chưa thấy bài hôm nay':page.status==='checking'?'Đang kiểm tra':page.status==='unknown'?'Chưa xác định':page.checked_at?'Cần kiểm tra hôm nay':'Chưa kiểm tra'
 const tone=page.status==='posted'?'green':['not_seen','checking'].includes(page.status)?'amber':page.status==='unknown'?'red':'gray'
 return <span className={'badge facebook-status '+tone} title={page.error_message||label}><i/>{label}</span>
}

export function FacebookOverview({selected,timezone,setTimezone,check,busy}:{selected:Set<number>;timezone:string;setTimezone:(v:string)=>void;check:(ids:number[])=>void;busy:boolean}){
 const {value:summary}=useServerRecord<Record<string,number>>('summary?zone='+encodeURIComponent(timezone))
 const posted=summary?.posted||0,missing=summary?.missing||0,pending=summary?.pending||0,active=summary?.active||0
 const zoneOptions=Array.from(new Set([timezone,'America/Los_Angeles','Asia/Ho_Chi_Minh','America/New_York','UTC']))
 return <section className="panel facebook-overview" aria-label="Theo dõi Facebook">
  <div className="facebook-overview-heading"><div className="facebook-mark"><Facebook size={20}/></div><div><h2>Facebook & Page</h2><p>Mỗi profile quản lý một hoặc nhiều Page · Chỉ đọc bài đã hiển thị</p></div><div className="facebook-sync-actions"><label>Múi giờ hôm nay<select aria-label="Múi giờ kiểm tra Facebook" value={timezone} onChange={e=>setTimezone(e.target.value)}>{zoneOptions.map(zone=><option key={zone}>{zone}</option>)}</select></label><button className="button primary" disabled={!selected.size||busy} onClick={()=>check([...selected])}><RefreshCw size={14}/>Kiểm tra đã chọn ({selected.size})</button></div></div>
  <div className="facebook-metrics"><span>Hôm nay: <strong>{facebookDay(timezone).check_date}</strong></span><span><Facebook size={14}/><strong>{summary?.accounts||0}</strong> tài khoản FB</span><span><strong>{summary?.pages||0}</strong> Page quản lý</span><span className="fb-green"><CheckCircle2 size={14}/><strong>{posted}</strong> Page đã đăng hôm nay</span><span className="fb-amber"><Clock3 size={14}/><strong>{missing}</strong> chưa thấy bài hôm nay</span><span><CircleHelp size={14}/><strong>{pending}</strong> cần kiểm tra{active>0?` · ${active} profile đang chờ/đọc`:''}</span></div>
 </section>
}

export function FacebookDialog({profile,timezone,check,stop,close}:{profile:Profile;timezone:string;check:(ids:number[])=>void;stop:(id:number)=>void;close:()=>void}){
 const [search,setSearch]=useState(''),[status,setStatus]=useState('all'),[page,setPage]=useState(1),[pageSize,setPageSize]=useState(10)
 const account=profile.facebook,active=facebookActive(profile)
 const unavailable=!active&&(['Running','Starting','Queued'].includes(profile.browser_status)||['Running','Pending'].includes(profile.task?.status||''))
 const data=useServerPage<FacebookPage>('facebook-pages',{profile_id:profile.id,q:search.trim(),status,zone:timezone},page,pageSize,setPage)
 const pages=data.items,currentPage=page,visible=pages,total=Number(data.summary?.all||0)
 useEffect(()=>{setSearch('');setStatus('all');setPage(1)},[profile.id])
 return <Modal wide title={`${profile.name} · Facebook & Page`} subtitle="Đọc bằng phiên Facebook đã đăng nhập trong profile này." close={close}>
  <div className="facebook-dialog-account"><Facebook size={25}/><div><strong>{account?.name||'Chưa đọc tên Facebook'}</strong><p>{accountLabels[account?.status||'unchecked']}{account?.checked_at?` · ${time(account.checked_at)}`:''}</p></div>{active?<button className="button" onClick={()=>stop(profile.id)}><Square size={13}/>Dừng kiểm tra</button>:<button className="button primary" disabled={unavailable} onClick={()=>check([profile.id])}><RefreshCw size={14}/>Kiểm tra Facebook</button>}</div>
  {account?.error_message&&<p className="facebook-notice" role="status">{account.error_message}</p>}
  <p className="hint facebook-help">Hôm nay theo {timezone}. Dừng Chrome của profile trước khi kiểm tra. Nếu Facebook yêu cầu xác minh, mở profile để xử lý rồi thử lại. Kết quả dựa trên bài đã tải được; số bài là mức tối thiểu quan sát được.</p>
  {data.error&&<p className="warning" role="alert">{data.error}</p>}
  {!!total&&<div className="table-tools facebook-detail-filters"><div className="search"><Search size={14}/><input aria-label="Tìm Page trong profile" placeholder="Tìm tên, ID hoặc link Page…" value={search} onChange={event=>{setSearch(event.target.value);setPage(1)}}/></div><select aria-label="Lọc trạng thái Page trong profile" value={status} onChange={event=>{setStatus(event.target.value);setPage(1)}}><option value="all">Tất cả trạng thái</option><option value="posted">Đã đăng hôm nay</option><option value="not_seen">Chưa thấy bài hôm nay</option><option value="unchecked">Chưa kiểm tra</option><option value="checking">Đang kiểm tra</option><option value="unknown">Chưa xác định</option></select></div>}
  {total?<><div className="facebook-page-cards" aria-label="Pages trong profile">{visible.map(page=><article className="facebook-page-card" key={page.id}><div className="facebook-page-card-heading"><a href={page.url} target="_blank" rel="noreferrer">{page.name}<ExternalLink size={13}/></a><FacebookStatus page={page}/></div><small>{page.checked_at?`Kiểm tra: ${time(page.checked_at)} · Ngày ${page.check_date} · ${page.timezone}`:'Chưa kiểm tra bài đăng'}{page.timezone&&page.timezone!==timezone?' · Khác múi giờ đang chọn':''}</small>{page.error_message&&<p>{page.error_message}</p>}{page.posts.length>0&&<div className="facebook-post-links">{page.posts.map((post,i)=><a key={post.url} href={post.url} target="_blank" rel="noreferrer">Bài hôm nay #{i+1}<ExternalLink size={12}/></a>)}</div>}</article>)}{!data.total&&<div className="facebook-no-pages">Không có Page phù hợp. Thử đổi từ khóa hoặc bộ lọc trạng thái.</div>}</div><Pagination total={data.total} page={currentPage} pageSize={pageSize} label="Pages trong profile" onPageChange={setPage} onPageSizeChange={size=>{setPageSize(size);setPage(1)}}/></>:<div className="facebook-no-pages"><CircleHelp size={24}/><p>{account?.status==='complete'?'Facebook chưa hiển thị Page nào trong danh sách quản lý.':'Chưa có danh sách Page. Nhấn Kiểm tra Facebook để đọc từ profile.'}</p></div>}
 </Modal>
}
