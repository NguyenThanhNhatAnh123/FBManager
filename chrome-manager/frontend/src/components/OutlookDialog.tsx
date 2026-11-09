import { useState, type FormEvent } from 'react'
import { Mail, Link as LinkIcon, LogIn, Square, ExternalLink, KeyRound } from 'lucide-react'
import type { Notify, Profile } from '../services/types'
import { api } from '../services/api'
import { Modal, time } from './ui'
import readerProviders from '../../../shared/recovery_readers.json'
import { outlookLabels as labels } from '../services/outlook'
import { useServerRecord } from '../hooks/useServerPage'
import { ResourceSelect } from './ResourceSelect'

function readerProvider(email:string){return readerProviders.find(p=>p.domains.includes(email.trim().toLowerCase().split('@')[1]||''))}
function suggestedReader(email:string){return readerProvider(email)?.url||''}

export function OutlookDialog({initialProfileId,notify,refresh,close}:{initialProfileId?:number;notify:Notify;refresh:()=>void;close:()=>void}){
 const [profileId,setProfileId]=useState(initialProfileId||0)
 const [email,setEmail]=useState(''),[password,setPassword]=useState(''),[recovery,setRecovery]=useState(''),[reader,setReader]=useState('')
 const [paste,setPaste]=useState(''),[code,setCode]=useState(''),[keep,setKeep]=useState(true),[busy,setBusy]=useState(false),[error,setError]=useState('')
 const {value:profile}=useServerRecord<Profile>(profileId?'profiles/'+profileId:null)
 const status=profile?.outlook,active=!!status?.active
 const unavailable=!profile||!active&&(['Running','Starting','Queued'].includes(profile.browser_status)||['queued','checking'].includes(profile.facebook?.status||'')||['Running','Pending'].includes(profile.task?.status||''))
 function changeRecovery(value:string){if(!reader||reader===suggestedReader(recovery))setReader(suggestedReader(value));setRecovery(value)}
 function parseLine(){
  const parts=paste.trim().split('|')
  if(parts.length<3||parts.length>4){setError('Dùng định dạng email|mật khẩu|email khôi phục, hoặc nhập từng ô.');return}
  setEmail(parts[0].trim());setPassword(parts[1]);setRecovery(parts[2].trim());setReader(parts[3]?.trim()||suggestedReader(parts[2]));setPaste('');setError('')
 }
 async function start(event:FormEvent){
  event.preventDefault();setBusy(true);setError('')
  try{await api('/outlook/start','POST',{profile_id:profileId,email,password,recovery_email:recovery,recovery_url:reader,keep_signed_in:keep});setPassword('');setPaste('');notify('Đã bắt đầu đăng nhập Outlook');refresh()}
  catch(e){setError((e as Error).message)}finally{setBusy(false)}
 }
 async function action(path:string,data?:unknown){
  setBusy(true);setError('')
  try{await api(path,'POST',data);setCode('');refresh()}
  catch(e){setError((e as Error).message)}finally{setBusy(false)}
 }
 const domain=recovery.trim().split('@')[1]||''
 return <Modal wide title="Outlook · Đăng nhập & lấy mã" subtitle="Dùng email khôi phục và website đọc mã do bạn chỉ định." close={close}>
  <div className="outlook-intro"><Mail size={22}/><p>Mỗi tài khoản dùng đúng Chrome profile đã chọn. Mật khẩu chỉ dùng cho lượt này, không lưu vào database hoặc log.</p></div>
  <form className="form-stack outlook-form" autoComplete="off" onSubmit={start}>
   <label>Chrome profile<ResourceSelect<Profile> path="profile-options" params={{scope:'proxy'}} label="Chrome profile Outlook" value={profileId} selected={profile} disabled={active||busy} onChange={id=>{setProfileId(id);setError('')}} display={item=>item.name} autoSelect/></label>
   <div className="outlook-paste"><label>Dán nhanh (tùy chọn)<input type="password" autoComplete="off" aria-label="Dòng email mật khẩu mail khôi phục" placeholder="email|mật khẩu|email khôi phục" value={paste} disabled={active||busy} onChange={e=>setPaste(e.target.value)}/></label><button className="button" type="button" disabled={!paste||active||busy} onClick={parseLine}>Tách vào các ô</button></div>
   <div className="outlook-fields"><label>Email Outlook / Hotmail<input type="email" aria-label="Email Outlook" autoComplete="off" value={email} required disabled={active||busy} onChange={e=>setEmail(e.target.value)} placeholder="tentaikhoan@hotmail.com"/></label><label>Mật khẩu<input type="password" aria-label="Mật khẩu Outlook" autoComplete="off" value={password} required disabled={active||busy} onChange={e=>setPassword(e.target.value)}/></label></div>
   <label>Email khôi phục<input type="email" aria-label="Email khôi phục" value={recovery} required disabled={active||busy} onChange={e=>changeRecovery(e.target.value)} placeholder="mailkhoiphuc@smvmail.com"/></label>
   <label><span><LinkIcon size={13}/> Link web lấy mã</span><input type="url" aria-label="Link web lấy mã" value={reader} disabled={active||busy} onChange={e=>setReader(e.target.value)} placeholder="https://website-doc-mail/..."/><small className="hint">{suggestedReader(recovery)?`Đã gợi ý link ${readerProvider(recovery)?.name}. Bạn có thể thay bằng link riêng.`:'Nhập trang đọc hộp thư khôi phục. Nếu chưa có link, có thể nhập mã thủ công.'} Dùng {'{email}'} tại vị trí địa chỉ mail trong link.</small></label>
   {domain&&<a className="outlook-search" href={'https://www.google.com/search?q='+encodeURIComponent(domain+' đọc mail inbox')} target="_blank" rel="noreferrer">Tìm web đọc mail cho {domain}<ExternalLink size={12}/></a>}
   <label className="check-label"><input type="checkbox" checked={keep} disabled={active||busy} onChange={e=>setKeep(e.target.checked)}/>Giữ phiên Microsoft trong Chrome profile</label>
   {unavailable&&<p className="outlook-note">Dừng Chrome, kiểm tra Facebook và các tác vụ của profile trước khi chạy đăng nhập tự động.</p>}
   {error&&<p role="alert" className="outlook-error">{error}</p>}
   {status&&<div className={'outlook-progress '+(status.status==='signed_in'?'success':'')} role="status"><strong>{labels[status.status]||status.status}</strong><span>{status.email}</span><p>{status.message}</p><small>{time(status.updated_at)}</small></div>}
   <p className="hint">Nếu Microsoft yêu cầu xác minh bổ sung, hoàn thành trực tiếp trong cửa sổ Chrome. Trang lấy mã có đăng nhập riêng cần được mở đúng hộp thư trước; chương trình không tự đăng nhập vào dịch vụ đó.</p>
   <div className="modal-actions outlook-actions"><button type="button" className="button" disabled={busy||active||!profile} onClick={()=>void action('/outlook/'+profileId+'/open')}><ExternalLink size={14}/>Mở Outlook</button>{active?<button type="button" className="button" disabled={busy} onClick={()=>void action('/outlook/'+profileId+'/stop')}><Square size={13}/>Dừng</button>:<button type="submit" className="button primary" disabled={busy||unavailable}><LogIn size={14}/>{busy?'Đang xử lý…':'Đăng nhập & lấy mã'}</button>}</div>
  </form>
  {status?.status==='waiting_code'&&<form className="outlook-manual" onSubmit={e=>{e.preventDefault();void action('/outlook/'+profileId+'/code',{code})}}><label><KeyRound size={14}/>Mã lấy thủ công (nếu cần)<input aria-label="Mã xác minh Outlook" inputMode="numeric" autoComplete="off" value={code} pattern="[0-9]{6,8}" required maxLength={8} onChange={e=>setCode(e.target.value.replace(/\D/g,''))}/></label><button className="button" disabled={busy||code.length<6}>Gửi mã vào Outlook</button></form>}
 </Modal>
}
