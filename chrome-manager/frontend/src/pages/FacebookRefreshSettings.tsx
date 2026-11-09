import {useEffect,useState} from 'react'
import {RefreshCw} from 'lucide-react'
import type {FacebookRefreshStatus} from '../services/types'
import {ensureChromeSession} from '../services/api'
import {identityRedirect} from '../services/identity'
import {watchWorkspaceUpdates} from '../hooks/useServerPage'
import './facebook-refresh-settings.css'

type ReadState={kind:'loading'}|{kind:'ready';data:FacebookRefreshStatus}|{kind:'unavailable';message:string}

function time(value:string|null|undefined){
 if(!value)return 'Chưa có lịch'
 const date=new Date(value)
 return Number.isNaN(date.getTime())?'Chưa xác định':new Intl.DateTimeFormat('vi-VN',{dateStyle:'short',timeStyle:'short'}).format(date)
}

function validStatus(value:FacebookRefreshStatus){
 return value&&typeof value.enabled==='boolean'&&value.interval_hours===24&&typeof value.running==='boolean'
  &&Number.isInteger(value.eligible_count)&&value.eligible_count>=0&&Number.isInteger(value.due_count)&&value.due_count>=0
  &&(value.next_due_at===null||typeof value.next_due_at==='string')&&Array.isArray(value.profiles)
}

export function FacebookRefreshSettings({enabled,onEnabledChange,disabled=false}:{enabled:boolean;onEnabledChange:(enabled:boolean)=>void;disabled?:boolean}){
 const [status,setStatus]=useState<ReadState>({kind:'loading'})
 useEffect(()=>{
  let mounted=true,inFlight=false,controller:AbortController|null=null
  async function read(){
   if(!mounted||inFlight)return
   inFlight=true;controller=new AbortController()
   const timeout=window.setTimeout(()=>controller?.abort(),15000)
   try{
    await ensureChromeSession()
    const response=await fetch('/api/v1/facebook/refresh-status',{method:'GET',credentials:'same-origin',cache:'no-store',signal:controller.signal,headers:{Accept:'application/json'}})
    const value=await response.json().catch(()=>({}))
    identityRedirect(response,value.code)
    if(!response.ok){
     if(mounted)setStatus({kind:'unavailable',message:response.status===404?'Server hiện chưa hỗ trợ tự động cập nhật Facebook & Page. Mở lại server sau khi cập nhật để dùng tính năng này.':'Chưa đọc được trạng thái tự động cập nhật từ server. Kiểm tra server rồi thử mở lại Settings.'})
     return
    }
    if(!validStatus(value))throw new Error('Unsupported status response')
    if(mounted)setStatus({kind:'ready',data:value})
   }catch{
    if(mounted)setStatus({kind:'unavailable',message:'Chưa đọc được trạng thái tự động cập nhật từ server. Kiểm tra server rồi thử mở lại Settings.'})
   }finally{window.clearTimeout(timeout);inFlight=false}
  }
  void read()
  const stop=watchWorkspaceUpdates(read)
  return ()=>{mounted=false;stop();controller?.abort()}
 },[])
 const data=status.kind==='ready'?status.data:null
 return <section className="panel settings-section facebook-refresh-settings" aria-labelledby="facebook-refresh-heading">
  <div className="section-icon"><RefreshCw size={20}/></div>
  <div>
   <h2 id="facebook-refresh-heading">Tự động cập nhật Facebook & Page</h2>
   <p className="muted">Đọc lại tên Facebook và tên các Page mỗi 24 giờ bằng Chrome chạy ẩn, không bật cửa sổ lên. Không kiểm tra lại bài viết trong lượt tự động này.</p>
   <label className="check-label"><input type="checkbox" checked={enabled} disabled={disabled||!data} onChange={event=>onEnabledChange(event.target.checked)}/>Tự động cập nhật tên Facebook và Page</label>
   <p className="hint">Thay đổi chỉ áp dụng khi bấm Save settings. Tính năng chỉ dùng Chrome profile thường có proxy được gán; không áp dụng cho profile BM.</p>
   {status.kind==='loading'?<p className="hint" role="status">Đang đọc trạng thái tính năng từ server…</p>:status.kind==='unavailable'?<p className="warning" role="status">{status.message}</p>:null}
   {data?<dl className="facebook-refresh-summary" aria-label="Trạng thái cập nhật tự động từ server">
    <div><dt>Thiết lập đã lưu</dt><dd>{data.enabled?'Đã bật':'Đã tắt'}</dd></div>
    <div><dt>Tiến trình</dt><dd>{data.running?data.active_profile_id?`Đang cập nhật Profile #${data.active_profile_id}`:'Đang cập nhật tên':data.enabled?'Đang chờ lượt':'Không lên lịch tự động'}</dd></div>
    <div><dt>Chu kỳ</dt><dd>Mỗi 24 giờ</dd></div>
    <div><dt>Profile đủ điều kiện / đến hạn</dt><dd>{data.eligible_count} / {data.due_count}</dd></div>
    <div><dt>Lượt tiếp theo · giờ máy này</dt><dd>{!data.enabled?'Đang tắt':data.next_due_at&&new Date(data.next_due_at).getTime()<=Date.now()?'Đã đến hạn · chờ profile và tài nguyên trống':time(data.next_due_at)}</dd></div>
    <div><dt>Hoàn tất gần nhất · giờ máy này</dt><dd>{data.last_finished_at?time(data.last_finished_at):'Chưa có dữ liệu'}</dd></div>
   </dl>:null}
   <p className="hint">Máy và server cần bật để chạy. Mỗi lần chỉ xử lý một profile; profile đang bận hoặc thiếu tài nguyên sẽ chờ, không bị đóng cưỡng ép. Lịch đến hạn được lưu qua lần mở lại server và được tiếp tục khi đủ điều kiện.</p>
   <p className="hint">Trạng thái cập nhật khi có thay đổi; tự đọc lại khi mở trang và khi kết nối trực tiếp gián đoạn.</p>
  </div>
 </section>
}
