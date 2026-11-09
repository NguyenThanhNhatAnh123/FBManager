import { Fragment, useEffect, useState, type FormEvent } from 'react'
import { Clock3, Image, Mail, Play, RefreshCw, Search, Square, UserPlus } from 'lucide-react'
import type { Notify, PageRecipientJob, PageRecipientMailCheck, PageRecipientSetup, Profile } from '../services/types'
import { checkRecipientMail, recipientConfig, resumeRecipient, sendRecipientCode, startRecipient, stopRecipient } from '../services/page_recipient'
import { Empty, time } from '../components/ui'
import { Pagination } from '../components/Pagination'
import { useServerPage, useServerRecord } from '../hooks/useServerPage'
import { ResourceSelect } from '../components/ResourceSelect'
import './page-recipient.css'

const fieldNames:Record<string,string>={avatar:'Ảnh đại diện',avatar_path:'Ảnh đại diện',cover:'Ảnh bìa',cover_path:'Ảnh bìa',bio:'Bio',address:'Địa chỉ',categories:'Danh mục',category:'Danh mục'}
const statusNames:Record<string,string>={queued:'Đang xếp hàng',pending:'Chờ xử lý',running:'Đang thực hiện',opening:'Đang mở Chrome',verifying_account:'Đang kiểm tra tài khoản',finding_invite:'Đang tìm lời mời',reviewing_invite:'Đang xem lời mời',accepting:'Đang nhận lời mời',waiting_code:'Chờ mã Outlook',entering_code:'Đang nhập mã',switching:'Đang chuyển Page',setup:'Đang setup Page',waiting_email:'Chờ email',waiting_manual_code:'Chờ nhập mã',paused:'Đã tạm dừng',manual_required:'Cần thao tác thủ công',needs_attention:'Cần kiểm tra',needs_confirmation:'Cần xác nhận lời mời',needs_user:'Cần thao tác trong Chrome',needs_outlook_login:'Cần đăng nhập Outlook',code_timeout:'Hết thời gian chờ mã',needs_invite_choice:'Cần chọn lời mời',needs_setup_data:'Chờ nội dung setup',waiting_page_choice:'Cần chọn lời mời',completed:'Hoàn tất',success:'Thành công',done:'Hoàn tất',partial:'Hoàn tất một phần',failed:'Lỗi',error:'Lỗi',stopped:'Đã dừng',cancelled:'Đã hủy',interrupted:'Bị gián đoạn',skipped:'Bỏ qua'}
const stepNames:Record<string,string>={notifications:'Mở thông báo lời mời',review_invite:'Review invite',next:'Next',accept:'Accept',check_email:'Check your email',waiting_code:'Chờ mã bảo mật Facebook trong Outlook',wait_code:'Chờ mã bảo mật Facebook trong Outlook',continue:'Continue',invite_accepted:'Invite Accepted',switch_page:'Switch now',switch_now:'Switch now',verify_page:'Xác minh đúng Page',setup:'Setup Page',ready_setup:'Đã nhận và chuyển Page · setup để sau',completed:'Hoàn tất'}
const pausedStates=new Set(['needs_confirmation','needs_user','needs_outlook_login','code_timeout','needs_invite_choice','needs_setup_data','interrupted'])
const mailFlagNames:Record<string,string>={sender_verified:'Người gửi phù hợp',recipient_verified:'Đúng email nhận',time_parsed:'Đọc được thời gian',time_fresh:'Thư mới đúng mốc',body_present:'Đọc được nội dung',matching_code_context:'Nội dung là mã Facebook'}
type MailCheckState={pending:boolean;started:number;result?:PageRecipientMailCheck;error?:string}
function label(value:string){return statusNames[value.toLowerCase()]||value.replaceAll('_',' ')}
function apiMessage(reason:unknown){const message=(reason as Error).message;return message==='Not Found'?'Server chưa nạp chức năng nhận Page. Mở lại start.bat rồi làm mới dashboard.':message}
function tone(status:string){const value=status.toLowerCase();return ['completed','success','done'].includes(value)?'success':['failed','error'].includes(value)?'error':pausedStates.has(value)||/wait|pause|manual|attention|confirmation|partial/.test(value)?'waiting':'neutral'}
function remaining(deadline:string|undefined|null,now:number){if(!deadline)return null;const value=Date.parse(deadline);return Number.isFinite(value)?Math.max(0,Math.ceil((value-now)/1000)):null}
function clock(seconds:number){return String(Math.floor(seconds/60)).padStart(2,'0')+':'+String(seconds%60).padStart(2,'0')}
function validCode(value:string){return /^[0-9]{6,8}$/.test(value.trim())}
function mailMessage(result:PageRecipientMailCheck){const value=result.message||'';return /[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}|[0-9]{4,}/i.test(value)?result.found?'Đã đọc được thư mã Facebook trong Outlook.':'Chưa đọc được thư mã Facebook trong Outlook.':value.slice(0,400)}
function pageLink(value:string){try{const url=new URL(value);return url.protocol==='https:'&&(url.hostname==='facebook.com'||url.hostname.endsWith('.facebook.com'))?url.href:null}catch{return null}}

interface Props {notify:Notify}
export function PageRecipientPage({notify}:Props){
 const [profileId,setProfileId]=useState(0)
 const [expectedUrl,setExpectedUrl]=useState(''),[expectedName,setExpectedName]=useState(''),[email,setEmail]=useState('')
 const [avatar,setAvatar]=useState(''),[cover,setCover]=useState(''),[bio,setBio]=useState(''),[categories,setCategories]=useState('')
 const [street,setStreet]=useState(''),[city,setCity]=useState(''),[postalCode,setPostalCode]=useState(''),[country,setCountry]=useState('')
 const [acceptOnly,setAcceptOnly]=useState(true),[assetRoot,setAssetRoot]=useState(''),[configError,setConfigError]=useState('')
 const [formError,setFormError]=useState('')
 const [starting,setStarting]=useState(false),[pending,setPending]=useState(new Set<number>()),[codes,setCodes]=useState<Record<number,string>>({}),[now,setNow]=useState(Date.now())
 const [mailChecks,setMailChecks]=useState<Record<number,MailCheckState>>({})
 const [search,setSearch]=useState(''),[status,setStatus]=useState('all'),[page,setPage]=useState(1),[pageSize,setPageSize]=useState(10)
 const data=useServerPage<PageRecipientJob>('recipient-jobs',{q:search.trim(),status},page,pageSize,setPage)
 const jobs=data.items,loading=data.loading,readError=data.error,refresh=data.refresh
 const {value:selected,refresh:refreshProfile}=useServerRecord<Profile>(profileId?'profiles/'+profileId:null)
 const active=!!selected?.recipient_active||jobs.some(job=>job.profile_id===profileId&&job.active)
 useEffect(()=>{
  let mounted=true
  void recipientConfig().then(config=>{if(mounted)setAssetRoot(config.asset_root)}).catch(reason=>{if(mounted)setConfigError(apiMessage(reason))})
  const tick=setInterval(()=>setNow(Date.now()),1000)
  return ()=>{mounted=false;clearInterval(tick)}
 },[])
 function setup():PageRecipientSetup{
  if(acceptOnly)return {}
  const address=Object.fromEntries(Object.entries({street:street.trim(),city:city.trim(),postal_code:postalCode.trim(),country:country.trim()}).filter(([,value])=>value))
  const list=[...new Set(categories.split(/\r?\n/).map(value=>value.trim()).filter(Boolean))]
  return {...(avatar.trim()?{avatar_path:avatar.trim()}:{}),...(cover.trim()?{cover_path:cover.trim()}:{}),...(bio.trim()?{bio:bio.trim()}:{}),...(Object.keys(address).length?{address}:{}),...(list.length?{categories:list}:{})}
 }
 async function start(event:FormEvent){
  event.preventDefault();setFormError('');setStarting(true)
  try{
   await startRecipient({profile_id:profileId,...(expectedUrl.trim()?{expected_page_url:expectedUrl.trim()}:{}),...(expectedName.trim()?{expected_page_name:expectedName.trim()}:{}),outlook_email:email.trim(),code_wait_seconds:300,setup:setup(),accept_only:acceptOnly})
   notify('Đã bắt đầu nhận Page cho '+selected?.name);await Promise.all([refresh(),refreshProfile()])
  }catch(reason){setFormError((reason as Error).message)}finally{setStarting(false)}
 }
 async function jobAction(job:PageRecipientJob,kind:'stop'|'resume'|'code'){
  if(kind==='code'&&!validCode(codes[job.profile_id]||'')){notify('Mã cần có 6–8 chữ số từ 0 đến 9.',true);return}
  setPending(old=>new Set(old).add(job.profile_id))
  try{
   if(kind==='code')await sendRecipientCode(job.profile_id,(codes[job.profile_id]||'').trim())
   else if(kind==='stop')await stopRecipient(job.profile_id)
   else await resumeRecipient(job.profile_id)
   notify(kind==='code'?'Đã gửi mã để tiếp tục':kind==='stop'?'Đã yêu cầu dừng tác vụ':'Đã yêu cầu tiếp tục tác vụ');await Promise.all([refresh(),refreshProfile()])
  }catch(reason){notify(kind==='code'?'Không gửi được mã. Kiểm tra trạng thái tác vụ và thử lại.':(reason as Error).message,true)}
  finally{if(kind==='code')setCodes(old=>({...old,[job.profile_id]:''}));setPending(old=>{const next=new Set(old);next.delete(job.profile_id);return next})}
 }
 async function checkMail(job:PageRecipientJob){
  const started=Date.now()
  setMailChecks(old=>({...old,[job.profile_id]:{pending:true,started}}))
  try{
   const result=await checkRecipientMail(job.profile_id)
   setMailChecks(old=>({...old,[job.profile_id]:{pending:false,started,result}}))
  }catch(reason){
   const error=(reason as Error).message==='OUTLOOK_MAIL_CHECK_TIMEOUT'?'Chưa nhận được kết quả kiểm tra sau 60 giây. Bạn có thể thử lại.':'Không kiểm tra được đọc thư. Dừng Chrome và tác vụ của profile rồi thử lại.'
   setMailChecks(old=>({...old,[job.profile_id]:{pending:false,started,error}}))
  }
 }
 const prepared=setup(),hasPrepared=Object.keys(prepared).length>0
 const ordered=jobs,currentPage=page,visible=jobs
 const statuses=(data.summary?.statuses||[]) as string[]
 return <div className="recipient-page">
  <section className="panel recipient-intro"><span className="recipient-intro-icon"><UserPlus size={23}/></span><div><h2>Nhận lời mời và chuẩn bị Page</h2><p>Lời mời → xác nhận email (tối đa 5 phút) → Switch now → xác minh Page → setup nội dung bạn nhập.</p></div></section>
  <div className="recipient-layout">
   <section className="panel recipient-form-panel"><div className="panel-title"><div><h2>Tác vụ mới</h2><p>Mỗi FB nhận một Page. Các mục setup để trống sẽ được bỏ qua.</p></div></div>
    {<form className="recipient-form" onSubmit={start}>
     <label>Profile FB nhận Page<ResourceSelect<Profile> path="profile-options" params={{scope:'regular'}} label="Profile nhận Page" value={profileId} selected={selected} disabled={starting} required onChange={setProfileId} autoSelect preferredId={3} display={profile=>profile.name+' · #'+String(profile.id).padStart(3,'0')}/></label>
     <div className="recipient-grid"><label>Tên Page dự kiến (tùy chọn)<input aria-label="Tên Page dự kiến" value={expectedName} disabled={starting} onChange={event=>setExpectedName(event.target.value)} placeholder="Để trống nếu chỉ có một lời mời"/></label><label>Link Page dự kiến (tùy chọn)<input aria-label="Link Page dự kiến" type="url" value={expectedUrl} disabled={starting} onChange={event=>setExpectedUrl(event.target.value)} placeholder="https://www.facebook.com/…"/></label></div>
     <p className="hint">Nếu có nhiều lời mời và chưa xác định được Page, tác vụ sẽ tạm dừng để bạn chọn.</p>
     <label>Email Outlook nhận mã Facebook<input aria-label="Email Outlook nhận mã" type="email" required autoComplete="off" value={email} disabled={starting} onChange={event=>setEmail(event.target.value)} placeholder="Email Outlook đã đăng nhập trong profile này"/><small className="hint">Dùng hộp thư Outlook đang đăng nhập trong đúng profile. Chờ thư mới tối đa 300 giây; có thể nhập mã thủ công khi cần.</small></label>
     <label className="check-label"><input type="checkbox" checked={acceptOnly} disabled={starting} onChange={event=>setAcceptOnly(event.target.checked)}/>Chỉ nhận Page, chưa setup</label>
     <fieldset className="recipient-setup" disabled={starting||acceptOnly}><legend>Setup sau khi xác minh đúng Page</legend>
      <div className="recipient-asset-note"><Image size={15}/><div><p>Nhập đường dẫn ảnh có sẵn trên máy. Ảnh chỉ được gửi lên Page khi tác vụ thực hiện setup.</p>{assetRoot&&<small>Thư mục ảnh: <code>{assetRoot}</code></small>}{configError&&<small className="recipient-error">Không đọc được thư mục ảnh: {configError}</small>}</div></div>
      <div className="recipient-grid"><label>Ảnh đại diện<input aria-label="Đường dẫn ảnh đại diện" value={avatar} onChange={event=>setAvatar(event.target.value)} placeholder="Đường dẫn file ảnh đại diện"/></label><label>Ảnh bìa<input aria-label="Đường dẫn ảnh bìa" value={cover} onChange={event=>setCover(event.target.value)} placeholder="Đường dẫn file ảnh bìa"/></label></div>
      <label>Bio<textarea aria-label="Bio Page" rows={3} value={bio} onChange={event=>setBio(event.target.value)} placeholder="Để trống để giữ nội dung hiện tại"/></label>
      <div className="recipient-grid"><label>Đường / số nhà<input aria-label="Địa chỉ đường" value={street} onChange={event=>setStreet(event.target.value)}/></label><label>Thành phố<input aria-label="Thành phố" value={city} onChange={event=>setCity(event.target.value)}/></label><label>Mã bưu chính<input aria-label="Mã bưu chính" value={postalCode} onChange={event=>setPostalCode(event.target.value)}/></label><label>Quốc gia<input aria-label="Quốc gia" value={country} onChange={event=>setCountry(event.target.value)}/></label></div>
      <label>Danh mục<textarea aria-label="Danh mục Page" rows={3} value={categories} onChange={event=>setCategories(event.target.value)} placeholder="Mỗi dòng một danh mục; để trống để bỏ qua"/></label>
     </fieldset>
     <div className="recipient-preview" aria-label="Nội dung setup chuẩn bị"><strong>Nội dung chuẩn bị</strong>{acceptOnly?<p>Chỉ nhận lời mời; không thay đổi nội dung Page.</p>:!hasPrepared?<p>Chưa nhập nội dung setup. Các mục trống được bỏ qua.</p>:<dl>{prepared.avatar_path&&<><dt>Ảnh đại diện</dt><dd>{prepared.avatar_path}</dd></>}{prepared.cover_path&&<><dt>Ảnh bìa</dt><dd>{prepared.cover_path}</dd></>}{prepared.bio&&<><dt>Bio</dt><dd>{prepared.bio}</dd></>}{prepared.address&&<><dt>Địa chỉ</dt><dd>{Object.values(prepared.address).join(', ')}</dd></>}{prepared.categories&&<><dt>Danh mục</dt><dd>{prepared.categories.join(' · ')}</dd></>}</dl>}</div>
     {formError&&<p className="recipient-error-box" role="alert">{formError}</p>}
     {active&&<p className="hint">Profile này đang có tác vụ nhận Page. Dừng hoặc hoàn tất tác vụ hiện tại trước khi bắt đầu lượt mới.</p>}
     <button className="button primary recipient-start" type="submit" disabled={starting||active||mailChecks[profileId]?.pending||!profileId}><Play size={15}/>{starting?'Đang bắt đầu…':acceptOnly?'Bắt đầu nhận Page':'Nhận Page & Setup'}</button>
    </form>}
   </section>
   <section className="recipient-jobs" aria-label="Tiến trình nhận Page"><div className="recipient-jobs-heading"><h2>Tiến trình nhận Page <span className="count">{Number(data.summary?.all||0)}</span></h2><button className="button small" type="button" onClick={()=>void refresh()}><RefreshCw size={13}/>Làm mới tiến trình</button></div>
    <div className="table-tools recipient-job-filters"><div className="search"><Search size={14}/><input aria-label="Tìm tiến trình nhận Page" placeholder="Tìm profile, Page hoặc tiến trình…" value={search} onChange={event=>{setSearch(event.target.value);setPage(1)}}/></div><select aria-label="Lọc trạng thái nhận Page" value={status} onChange={event=>{setStatus(event.target.value);setPage(1)}}><option value="all">Tất cả trạng thái</option>{statuses.map(value=><option key={value} value={value}>{label(value)}</option>)}</select></div>
    {readError&&<p className="recipient-error-box" role="alert">{readError}</p>}
    {loading?<p className="loading">Đang tải tiến trình…</p>:!ordered.length?<section className="panel recipient-empty"><Empty title={Number(data.summary?.all)?'Không có tiến trình phù hợp':'Sẵn sàng nhận Page'} detail={Number(data.summary?.all)?'Thử đổi từ khóa hoặc bộ lọc trạng thái.':'Chọn profile, email nhận mã và nội dung setup rồi bắt đầu.'}/></section>:visible.map(job=>{
     const confirmation=job.status.toLowerCase()==='needs_confirmation',paused=pausedStates.has(job.status.toLowerCase())||/pause|manual|attention|choice|confirmation/.test(job.status.toLowerCase()),needsCode=['waiting_code','code_timeout','waiting_email','waiting_manual_code'].includes(job.status.toLowerCase()),seconds=remaining(job.code_deadline,now),mailCheck=mailChecks[job.profile_id],busy=pending.has(job.profile_id)||!!mailCheck?.pending
     const profile=job.profile||(selected?.id===job.profile_id?selected:null),mailAllowed=profile?.browser_status==='Stopped'&&!job.active&&!profile.outlook?.active&&!['Running','Pending'].includes(profile.task?.status||'')&&!['queued','checking'].includes(profile.facebook?.status||'')
     const flags=Object.entries(mailFlagNames).filter(([key])=>typeof mailCheck?.result?.diagnostics?.[key]==='boolean')
     const mailReadSeconds=mailCheck?Math.min(30,Math.max(0,30-Math.floor((now-mailCheck.started)/1000))):0
     return <article className="panel recipient-job" key={job.id} aria-label={'Tác vụ của '+job.profile_name}>
      <div className="recipient-job-header"><div><h3>{job.profile_name}</h3><small>Profile #{String(job.profile_id).padStart(3,'0')}</small></div><span className={'recipient-state '+tone(job.status)} aria-label={'Trạng thái tác vụ của '+job.profile_name}>{label(job.status)}</span></div>
      <div className="recipient-current-step"><span>{stepNames[job.step.toLowerCase()]||job.step.replaceAll('_',' ')}</span>{job.message&&<p>{job.message}</p>}</div>
      {(job.page_name||job.page_id)&&<div className="recipient-page-identity"><UserPlus size={15}/><div><strong>{job.page_name||'Page đang xử lý'}</strong>{job.page_id&&<small>ID: {job.page_id}</small>}</div></div>}
      {confirmation&&<p className="recipient-confirmation">Kiểm tra tên Page, người mời và quyền được cấp trong thông tin phía trên trước khi xác nhận nhận Page.</p>}
      {!!job.candidates?.length&&<div className="recipient-candidates"><strong>Các lời mời tìm thấy</strong><ul>{job.candidates.map((candidate,index)=>{const href=pageLink(candidate.url);return <li key={index}><span>{candidate.name||'Page chưa có tên'}</span>{href?<a href={href} target="_blank" rel="noreferrer">{candidate.url}</a>:<small>{candidate.url}</small>}</li>})}</ul><p className="hint">Điền Link Page dự kiến ở form để xác định đúng lời mời.</p></div>}
      {needsCode&&<div className="recipient-code-status"><Mail size={17}/><div><strong>Chờ mã bảo mật Facebook trong Outlook</strong>{seconds!==null&&<p aria-label={'Thời gian chờ mã của '+job.profile_name}><Clock3 size={12}/>{seconds>0?'Còn '+clock(Math.min(seconds,300))+' / 05:00':'Đã hết 5 phút chờ mã'}</p>}</div></div>}
      {needsCode&&<form className="recipient-code-form" onSubmit={event=>{event.preventDefault();void jobAction(job,'code')}}><label>Mã thủ công<input type="password" aria-label={'Mã thủ công cho '+job.profile_name} autoComplete="new-password" inputMode="numeric" pattern="[0-9]{6,8}" minLength={6} maxLength={8} required value={codes[job.profile_id]||''} disabled={busy} onChange={event=>setCodes(old=>({...old,[job.profile_id]:event.target.value}))} placeholder="Nhập mã 6–8 chữ số"/></label><button className="button" disabled={busy||!validCode(codes[job.profile_id]||'')}>Gửi mã và tiếp tục</button><small className="hint">Mã chỉ dùng cho tác vụ hiện tại và được xóa khỏi ô sau khi gửi.</small></form>}
      <div className="recipient-field-results"><h4>Kết quả setup</h4>{Object.keys(job.fields||{}).length?<dl>{Object.entries(job.fields||{}).map(([field,result])=><div key={field}><dt>{fieldNames[field]||field.replaceAll('_',' ')}</dt><dd><span className={'recipient-state '+tone(result.status)}>{label(result.status)}</span>{result.message&&<p>{result.message}</p>}</dd></div>)}</dl>:<p className="hint">Kết quả từng mục sẽ hiện sau khi xác minh đúng Page.</p>}</div>
      <div className="recipient-job-actions">{(job.active||paused)&&<button className="button" type="button" aria-label={'Dừng tác vụ của '+job.profile_name} disabled={busy} onClick={()=>void jobAction(job,'stop')}><Square size={13}/>Dừng tác vụ</button>}{paused&&<button className="button primary" type="button" aria-label={(confirmation?'Xác nhận nhận Page cho ':'Tiếp tục tác vụ của ')+job.profile_name} disabled={busy} onClick={()=>void jobAction(job,'resume')}><Play size={13}/>{confirmation?'Xác nhận nhận Page':'Tiếp tục'}</button>}</div>
      {paused&&!confirmation&&<p className="hint">Hoàn tất bước được yêu cầu trong Chrome rồi nhấn Tiếp tục.</p>}
      <div className="recipient-job-actions"><button className="button" type="button" aria-label={'Kiểm tra đọc mã Outlook của '+job.profile_name} title={mailAllowed?'Chỉ kiểm tra đọc thư trong Outlook':'Dừng Chrome và tác vụ của profile trước khi kiểm tra đọc thư'} disabled={busy||!mailAllowed} onClick={()=>void checkMail(job)}><Mail size={14}/>{mailCheck?.pending?'Đang kiểm tra…':'Kiểm tra đọc mã Outlook'}</button></div>
      <p className="hint">Chỉ kiểm tra đọc thư, không gửi/nhận lời mời hoặc nhập mã vào Facebook.</p>
      {mailCheck&&<div className="recipient-preview" aria-label={'Kết quả kiểm tra đọc mã Outlook của '+job.profile_name}>{mailCheck.pending?<p role="status">{mailReadSeconds>0?'Đang kiểm tra đọc thư · còn tối đa '+mailReadSeconds+' giây.':'Đang hoàn tất kiểm tra...'}</p>:mailCheck.error?<p className="recipient-error" role="status">{mailCheck.error}</p>:mailCheck.result&&<><strong>{mailCheck.result.found?'Đọc được thư mã Facebook':'Chưa đọc được thư mã Facebook'}</strong><p>{mailMessage(mailCheck.result)}</p>{flags.length>0&&<details><summary className="hint">Chi tiết kiểm tra</summary><dl>{flags.map(([key,name])=><Fragment key={key}><dt>{name}</dt><dd>{mailCheck.result?.diagnostics?.[key]?'Có':'Chưa xác nhận'}</dd></Fragment>)}</dl></details>}</>}</div>}
      <p className="recipient-job-time">Bắt đầu: {time(job.started_at)} · Cập nhật: {time(job.updated_at)}</p>
     </article>
    })}
    <Pagination total={data.total} page={currentPage} pageSize={pageSize} label="tiến trình nhận Page" onPageChange={setPage} onPageSizeChange={size=>{setPageSize(size);setPage(1)}}/>
   </section>
  </div>
 </div>
}
