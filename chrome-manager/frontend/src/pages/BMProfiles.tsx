import { useEffect, useState, type FormEvent } from 'react'
import { ArrowLeft, Building2, Cable, ExternalLink, FileText, Globe, Pencil, Play, Plus, RefreshCw, Search, Smartphone, Square } from 'lucide-react'
import type { BmPortfolio, Notify, Profile } from '../services/types'
import { api } from '../services/api'
import { Badge, Empty, Modal, Submit, time } from '../components/ui'
import { Pagination } from '../components/Pagination'
import { useServerPage, useServerRecord } from '../hooks/useServerPage'
import { ResourceSelect } from '../components/ResourceSelect'
import './bm-profiles.css'

interface Props {
 notify:Notify;refresh:()=>void
 act:(path:string,data?:unknown,method?:string)=>Promise<boolean>
 edit:(profile:Profile)=>void;logs:(profile:Profile)=>void
}
type Editor={mode:'existing'|'new'|'metadata';profile?:Profile}

function busyTask(profile:Profile){
 return !!profile.recipient_active||!!profile.outlook?.active||['queued','checking'].includes(profile.facebook?.status||'')||['Running','Pending'].includes(profile.task?.status||'')
}

function browserActive(profile:Profile){
 return ['Running','Starting','Queued'].includes(profile.browser_status)
}

function parsePortfolios(value:string):BmPortfolio[]{
 const seen=new Set<string>()
 return value.split(/\r?\n/).map((line,index)=>({line:line.trim(),number:index+1})).filter(row=>row.line).map(({line,number})=>{
  const [raw,...parts]=line.split('|'),bm_id=raw.trim(),name=parts.join('|').trim()
  if(!/^[1-9]\d{0,29}$/.test(bm_id))throw new Error(`Dòng ${number}: BM ID phải là số nguyên dương, tối đa 30 chữ số.`)
  if(seen.has(bm_id))throw new Error(`BM ID ${bm_id} bị lặp trong danh sách.`)
  if(name.length>120)throw new Error(`Dòng ${number}: tên BM tối đa 120 ký tự.`)
  seen.add(bm_id)
  return {bm_id,name}
 })
}

function BMProfileEditor({editor,notify,refresh,close}:{editor:Editor;notify:Notify;refresh:()=>void;close:()=>void}){
 const [selectedId,setProfileId]=useState<number|null>(editor.profile?.id??null)
 const profileId=selectedId||0
 const {value:candidate}=useServerRecord<Profile>(editor.mode==='existing'&&profileId?'profiles/'+profileId:null)
 const canAssign=!!candidate&&!browserActive(candidate)&&!busyTask(candidate)
 const [name,setName]=useState('')
 const [portfolios,setPortfolios]=useState((editor.profile?.bm?.portfolios||[]).map(row=>row.bm_id+(row.name?'|'+row.name:'')).join('\n'))
 const [note,setNote]=useState(editor.profile?.bm?.note||'')
 const [busy,setBusy]=useState(false),[error,setError]=useState('')
 async function submit(event:FormEvent){
  event.preventDefault();setError('')
  if(editor.mode==='existing'&&!canAssign)return
  let rows:BmPortfolio[]
  try{rows=parsePortfolios(portfolios)}catch(reason){setError((reason as Error).message);return}
  setBusy(true)
  try{
   const bm={portfolios:rows,note:note.trim()}
   if(editor.mode==='new')await api('/profiles','POST',{name:name.trim(),proxy_id:null,bm})
   else await api('/bm-profiles/'+profileId,'PUT',bm)
   notify(editor.mode==='metadata'?'Đã lưu thông tin BM':editor.mode==='new'?'Đã tạo profile BM':'Đã chuyển profile sang BM Profiles')
   refresh();close()
  }catch(reason){setError((reason as Error).message)}finally{setBusy(false)}
 }
 const title=editor.mode==='new'?'Tạo profile BM':editor.mode==='metadata'?'Thông tin BM · '+editor.profile?.name:'Thêm profile cầm BM'
 return <Modal wide title={title} subtitle="Ghi nhận BM của bạn và mở bằng đúng Chrome profile." close={()=>{if(!busy)close()}}>
  <form className="form-stack bm-profile-form" onSubmit={submit}>
   {editor.mode==='existing'&&<label>Chrome profile có sẵn
    <ResourceSelect<Profile> path="profile-options" params={{scope:'regular'}} label="Chọn profile cầm BM" value={profileId} selected={candidate} disabled={busy} required autoSelect onChange={setProfileId} optionDisabled={profile=>browserActive(profile)||busyTask(profile)} display={profile=>profile.name+(profile.facebook?.name?' · '+profile.facebook.name:'')+(browserActive(profile)||busyTask(profile)?' · Đang sử dụng':'')}/>
    <small className="hint">Dừng Chrome và các tác vụ của profile trước khi chuyển sang BM Profiles.</small>
   </label>}
   {editor.mode==='existing'&&candidate?.proxy_id&&<p className="hint">Proxy đang gán sẽ được giữ cho profile BM và tách khỏi Proxy Manager thường.</p>}
   {editor.mode==='new'&&<><label>Tên Chrome profile<input aria-label="Tên profile BM" required maxLength={80} disabled={busy} value={name} onChange={event=>setName(event.target.value)} placeholder="Ví dụ: BM Chính"/></label><p className="hint">Tạo Chrome mới hoàn toàn cho BM. Sau khi tạo, thêm Proxy riêng trước khi mở Chrome; proxy này dùng cho tất cả website.</p></>}
   {editor.mode==='metadata'&&<div className="bm-editor-profile"><Globe size={16}/><strong>{editor.profile?.name}</strong><span>#{String(profileId).padStart(3,'0')}</span></div>}
   <label>Danh sách BM <span className="field-meta">{portfolios.split(/\r?\n/).filter(line=>line.trim()).length} BM</span><textarea aria-label="Danh sách BM" rows={5} disabled={busy} value={portfolios} onChange={event=>setPortfolios(event.target.value)} placeholder={'Mỗi dòng: BM ID|Tên BM (tên tùy chọn)\n123456789012345|BM cửa hàng'}/><small className="hint">Có thể để trống và bổ sung sau. Danh sách do bạn nhập, chưa phải kết quả quét hoặc xác minh quyền trên Facebook.</small></label>
   <label>Ghi chú<textarea aria-label="Ghi chú profile BM" rows={3} maxLength={4000} disabled={busy} value={note} onChange={event=>setNote(event.target.value)} placeholder="Ghi chú để phân biệt các profile cầm BM"/></label>
   {error&&<p role="alert" className="bm-form-error">{error}</p>}
   <div className="modal-actions"><button className="button" type="button" disabled={busy} onClick={close}>Hủy</button><Submit busy={busy} disabled={editor.mode==='existing'&&!canAssign} label={editor.mode==='metadata'?'Lưu thông tin':editor.mode==='new'?'Tạo profile BM':'Thêm vào BM Profiles'}/></div>
  </form>
 </Modal>
}

function BMProxyEditor({profile,notify,refresh,close}:{profile:Profile;notify:Notify;refresh:()=>void;close:()=>void}){
 const [content,setContent]=useState(''),[protocol,setProtocol]=useState(profile.proxy?.protocol||'http')
 const [busy,setBusy]=useState(false),[error,setError]=useState('')
 const running=browserActive(profile)||busyTask(profile)
 const proxy=profile.proxy,checking=!!proxy?.checking
 async function save(event:FormEvent){
  event.preventDefault();setError('');setBusy(true)
  try{
   await api('/bm-profiles/'+profile.id+'/proxy','PUT',{content:content.trim(),default_protocol:protocol,check_after_save:true})
   setContent('');notify('Đã lưu proxy riêng cho BM và xếp hàng kiểm tra');refresh();close()
  }catch(reason){setError((reason as Error).message)}finally{setBusy(false)}
 }
 async function action(method:'POST'|'DELETE'){
  setError('');setBusy(true)
  try{
   await api('/bm-profiles/'+profile.id+'/proxy'+(method==='POST'?'/check':''),method)
   notify(method==='POST'?'Đã xếp hàng kiểm tra proxy BM':'Đã gỡ proxy riêng khỏi profile BM');refresh()
   if(method==='DELETE')close()
  }catch(reason){setError((reason as Error).message)}finally{setBusy(false)}
 }
 return <Modal title={'Proxy riêng · '+profile.name} subtitle="Proxy BM dùng cho tất cả website, kể cả khi cài đặt chung chỉ dùng proxy cho Facebook." close={()=>{if(!busy)close()}}>
  <p className="hint">Kiểm tra proxy BM tự nhận IPv4/IPv6, độc lập lựa chọn IP của nhóm thường.</p>
  {proxy?<div className="bm-current-proxy"><div><strong className="mono">{proxy.host}:{proxy.port}</strong><Badge status={checking?'Checking':proxy.status}/></div><small>{proxy.protocol.toUpperCase()}{proxy.has_credentials?' · Có xác thực':''}</small><small>IP: {proxy.ip_address||'Chưa kiểm tra'}</small><small>Kiểm tra lần cuối: {time(proxy.last_checked)}</small>{proxy.error_message&&!checking&&<p className="bm-proxy-error">{proxy.error_message}</p>}<div className="bm-proxy-current-actions"><button className="button small" type="button" disabled={busy||checking} onClick={()=>void action('POST')}><RefreshCw size={13}/>{checking?'Đang kiểm tra…':'Kiểm tra proxy hiện tại'}</button><button className="text-button" type="button" disabled={busy||checking||running} onClick={()=>void action('DELETE')}>Gỡ proxy khỏi profile</button></div></div>:<p className="bm-private-proxy-note">Profile BM chưa có proxy riêng.</p>}
  <form className="form-stack bm-private-proxy-form" autoComplete="off" onSubmit={save}>
   <label>Giao thức<select aria-label="Giao thức proxy BM" value={protocol} disabled={busy||running} onChange={event=>setProtocol(event.target.value)}><option value="http">HTTP</option><option value="https">HTTPS</option><option value="socks5">SOCKS5</option></select></label>
   <label>{proxy?'Proxy riêng mới':'Proxy riêng BM'}<input type="password" aria-label="Proxy riêng BM" autoComplete="new-password" value={content} required maxLength={2048} disabled={busy||running} onChange={event=>setContent(event.target.value)} placeholder="host:port:username:password"/><small className="hint">Nhập một proxy: host:port, host:port:username:password hoặc URL HTTP/HTTPS/SOCKS5. Thông tin xác thực đã lưu được giữ kín; chỉ nhập ô này khi thêm hoặc thay proxy.</small></label>
   {running&&<p className="bm-private-proxy-note">Dừng Chrome và tác vụ của profile trước khi thêm, thay hoặc gỡ proxy. Bạn vẫn có thể kiểm tra proxy hiện tại.</p>}
   {error&&<p role="alert" className="bm-form-error">{error}</p>}
   <div className="modal-actions"><button className="button" type="button" disabled={busy} onClick={close}>Đóng</button><Submit busy={busy} disabled={running||!content.trim()} label="Lưu và kiểm tra"/></div>
  </form>
 </Modal>
}

export function BMProfilesPage({notify,refresh,act,edit,logs}:Props){
 const [editor,setEditor]=useState<Editor|null>(null)
 const [proxyProfileId,setProxyProfileId]=useState<number|null>(null)
 const [search,setSearch]=useState(''),[filter,setFilter]=useState('all')
 const [page,setPage]=useState(1),[pageSize,setPageSize]=useState(10),[pending,setPending]=useState(new Set<number>())
 const data=useServerPage<Profile>('profiles',{scope:'bm',q:search.trim(),status:filter},page,pageSize,setPage)
 const bmProfiles=data.items,current=page,visible=data.items
 const {value:proxyProfile}=useServerRecord<Profile>(proxyProfileId===null?null:'profiles/'+proxyProfileId,bmProfiles.find(profile=>profile.id===proxyProfileId)||null)
 useEffect(()=>setPage(1),[search,filter,pageSize])
 async function action(profile:Profile,path:string,data?:unknown,method?:string){
  setPending(old=>new Set(old).add(profile.id))
  try{await act(path,data,method)}finally{setPending(old=>{const next=new Set(old);next.delete(profile.id);return next})}
 }
 async function removeRole(profile:Profile){
  setPending(old=>new Set(old).add(profile.id))
  try{if(await act('/bm-profiles/'+profile.id,undefined,'DELETE'))notify('Đã chuyển '+profile.name+' về Chrome Profiles. Dữ liệu Chrome được giữ lại.')}
  finally{setPending(old=>{const next=new Set(old);next.delete(profile.id);return next})}
 }
 async function toggleMobile(profile:Profile){
  const enabled=!profile.bm?.mobile_enabled
  setPending(old=>new Set(old).add(profile.id))
  try{if(await act('/bm-profiles/'+profile.id+'/mobile',{enabled},'PUT'))notify(enabled?'Đã bật chế độ điện thoại cho lần mở kế tiếp':'Đã tắt chế độ điện thoại cho lần mở kế tiếp')}
  finally{setPending(old=>{const next=new Set(old);next.delete(profile.id);return next})}
 }
 return <div className="bm-profiles-page">
  <section className="panel bm-summary"><div className="bm-summary-icon"><Building2 size={23}/></div><div><h2>Profile quản lý BM</h2><p>Quản lý các Chrome profile cầm BM trong danh sách riêng.</p></div><div className="bm-summary-counts"><span><strong>{Number(data.summary?.all||0)}</strong> profile</span><span><strong>{Number(data.summary?.portfolios||0)}</strong> BM đã ghi nhận</span></div></section>
  <section className="panel">
   <div className="panel-title bm-panel-title"><div><h2>BM Profiles <span className="count">{Number(data.summary?.all||0)}</span></h2><p>Proxy riêng BM dùng cho tất cả website, kể cả khi cài đặt chung chỉ dùng proxy cho Facebook.</p></div><div className="bm-page-actions"><button className="button" onClick={()=>setEditor({mode:'existing'})}><Plus size={15}/>Thêm profile có sẵn</button><button className="button primary" onClick={()=>setEditor({mode:'new'})}><Plus size={15}/>Tạo profile BM</button></div></div>
   <p className="bm-mobile-note"><Smartphone size={14}/><span>Bật Điện thoại khi Chrome đã dừng để lần mở kế tiếp tự dùng khung nhìn, cảm ứng và User-Agent điện thoại, không cần F12.</span></p>
   {data.error&&<p className="warning" role="alert">{data.error}</p>}
   <div className="table-tools bm-table-tools"><div className="search"><Search size={15}/><input aria-label="Tìm profile BM" placeholder="Tìm tên profile, tên FB, BM ID, tên BM…" value={search} onChange={event=>setSearch(event.target.value)}/></div><select aria-label="Lọc trạng thái browser BM" value={filter} onChange={event=>setFilter(event.target.value)}><option value="all">Tất cả trạng thái</option>{['Running','Stopped','Starting','Queued','Error'].map(status=><option key={status}>{status}</option>)}</select></div>
   {!data.total?<Empty title="Chưa có profile cầm BM" detail="Chọn một Chrome profile có sẵn hoặc tạo profile BM mới để quản lý tại đây."><button className="button" onClick={()=>setEditor({mode:'existing'})}><Plus size={15}/>Chọn profile có sẵn</button></Empty>:<div className="table-scroll"><table className="bm-profile-table"><thead><tr><th>Chrome profile</th><th>Tên Facebook</th><th>BM đã ghi nhận</th><th>Proxy / IP</th><th>Browser</th><th>Ghi chú</th><th>Thao tác</th></tr></thead><tbody>{visible.map(profile=>{
     const working=busyTask(profile),browserBusy=browserActive(profile),busy=pending.has(profile.id)
    const stopping=browserBusy||working,needsProxy='Thêm proxy riêng trước khi mở Chrome hoặc BM'
    const canOpenBM=!!profile.proxy&&!working&&!(browserBusy&&profile.browser_mode==='automation')
     const portfolios=profile.bm?.portfolios||[]
     const singleBM=portfolios.length===1?portfolios[0]:null
     const openBMPath='/bm-profiles/'+profile.id+'/open'
     const openBMTitle=!profile.proxy?needsProxy:!canOpenBM?'Dừng tác vụ tự động trước khi mở BM':singleBM?'Mở BM '+singleBM.bm_id+' trong '+profile.name:'Mở trang cài đặt BM trong '+profile.name
    return <tr key={profile.id}>
     <td><div className="bm-profile-name"><span className={'browser-avatar '+(profile.browser_status==='Running'?'active':'')}><Globe size={17}/></span><div><strong>{profile.name}</strong><small className="muted">#{String(profile.id).padStart(3,'0')}</small>{profile.bm?.mobile_enabled&&<small className="bm-mobile-preset"><Smartphone size={11}/>Điện thoại · tự mở</small>}</div></div></td>
     <td className="bm-facebook-name">{profile.facebook?.name||<span className="muted">Chưa đọc tên FB</span>}</td>
      <td className="bm-portfolios">
       {portfolios.length?<div className="bm-portfolio-list">
        {portfolios.map(portfolio=><div className="bm-portfolio-item" key={portfolio.bm_id}>
         <div><strong>{portfolio.name||'BM '+portfolio.bm_id}</strong><small className="mono">{portfolio.bm_id}</small></div>
         <button className="icon-button" title={!profile.proxy?needsProxy:'Mở BM '+portfolio.bm_id+' trong '+profile.name} aria-label={'Mở BM '+portfolio.bm_id+' trong '+profile.name} disabled={busy||!canOpenBM} onClick={()=>void action(profile,openBMPath,{bm_id:portfolio.bm_id})}><ExternalLink size={14}/></button>
        </div>)}
       </div>:<span className="muted">Chưa ghi BM ID</span>}
       {portfolios.length>1&&<small className="hint">Chọn nút mở cạnh BM bạn muốn dùng.</small>}
      </td>
     <td>{profile.proxy?<div className="bm-proxy"><span className="mono">{profile.proxy.host}:{profile.proxy.port}</span><small>{profile.proxy.protocol.toUpperCase()}{profile.proxy.has_credentials?' · Có xác thực':''}</small><small className="mono">{profile.proxy.ip_address||'Chưa kiểm tra IP'}</small><Badge status={profile.proxy.checking?'Checking':profile.proxy.status}/>{profile.proxy.error_message&&!profile.proxy.checking&&<small className="bm-proxy-error">{profile.proxy.error_message}</small>}</div>:<span className="muted">Chưa có proxy riêng</span>}</td>
     <td title={profile.browser_error||''}><Badge status={profile.browser_status}/>{profile.browser_mode&&<small className="bm-browser-mode muted">{profile.browser_mode==='manual'?'Manual browsing':profile.browser_mode==='mobile'?'Chế độ điện thoại':'Automation'}</small>}{working&&<small className="bm-browser-mode muted">Tác vụ đang chạy</small>}</td>
     <td className="bm-note" title={profile.bm?.note||''}><p>{profile.bm?.note||'—'}</p><small className="muted">{time(profile.bm?.updated_at||null)}</small></td>
      <td><div className="bm-row-actions">
       <button className={'row-open '+(stopping?'stop':'')} aria-label={(stopping?'Dừng ':'Mở ')+profile.name} title={!stopping&&!profile.proxy?needsProxy:stopping?'Dừng Chrome và tác vụ của profile':'Mở Chrome bằng proxy riêng BM'} disabled={busy||(!stopping&&!profile.proxy)} onClick={()=>void action(profile,'/profiles/'+profile.id+(stopping?'/stop':'/start'))}>{stopping?<Square size={12}/>:<Play size={12}/>}<span>{stopping?'Dừng':'Mở Chrome'}</span></button>
       {portfolios.length<2&&<button className="button small" title={openBMTitle} disabled={busy||!canOpenBM} onClick={()=>void action(profile,openBMPath,singleBM?{bm_id:singleBM.bm_id}:undefined)}><ExternalLink size={13}/>{singleBM?'Mở BM':'Mở cài đặt BM'}</button>}
       <button className={'button small bm-mobile-toggle'+(profile.bm?.mobile_enabled?' active':'')} aria-label={'Điện thoại của '+profile.name} aria-pressed={!!profile.bm?.mobile_enabled} title={browserBusy||working?'Dừng Chrome và tác vụ trước khi đổi chế độ điện thoại':profile.bm?.mobile_enabled?'Tắt chế độ điện thoại cho lần mở kế tiếp':'Tự dùng khung nhìn, cảm ứng và User-Agent điện thoại khi mở, không cần F12'} disabled={busy||browserBusy||working} onClick={()=>void toggleMobile(profile)}><Smartphone size={13}/>Điện thoại</button>
       <button className="button small" aria-label={'Proxy riêng của '+profile.name} title="Quản lý proxy riêng cho profile BM" disabled={busy} onClick={()=>setProxyProfileId(profile.id)}><Cable size={13}/>Proxy riêng</button>
       <button className="icon-button" aria-label={'Sửa thông tin BM của '+profile.name} title="Sửa thông tin BM" disabled={busy} onClick={()=>setEditor({mode:'metadata',profile})}><Building2 size={14}/></button>
       <button className="icon-button" aria-label={'Sửa profile '+profile.name} title="Sửa tên profile" disabled={busy||browserBusy||working} onClick={()=>edit(profile)}><Pencil size={14}/></button>
       <button className="icon-button" aria-label={'Nhật ký '+profile.name} title="Nhật ký profile" onClick={()=>logs(profile)}><FileText size={14}/></button>
       <button className="icon-button" aria-label={'Chuyển '+profile.name+' về Chrome Profiles'} title={profile.proxy?'Gỡ proxy riêng trước khi chuyển về Chrome Profiles':'Chuyển về Chrome Profiles; giữ nguyên thư mục và dữ liệu Chrome'} disabled={busy||browserBusy||working||!!profile.proxy} onClick={()=>void removeRole(profile)}><ArrowLeft size={14}/></button>
      </div></td>
    </tr>
   })}{!data.total&&<tr><td className="no-matches" colSpan={7}>Không có profile BM phù hợp.</td></tr>}</tbody></table></div>}
   <Pagination total={data.total} page={current} pageSize={pageSize} label="BM profiles" onPageChange={setPage} onPageSizeChange={value=>{setPageSize(value);setPage(1)}}/>
  </section>
  {editor&&<BMProfileEditor editor={editor} notify={notify} refresh={refresh} close={()=>setEditor(null)}/>}
  {proxyProfile&&<BMProxyEditor key={proxyProfile.id} profile={proxyProfile} notify={notify} refresh={refresh} close={()=>setProxyProfileId(null)}/>}
 </div>
}
