import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from 'react'
import { CalendarClock, CheckCircle2, Film, RefreshCw, Search, Upload, X } from 'lucide-react'
import type { Notify, Profile, ReelAsset, ReelPageOption, ReelPostingEligibility, ReelSchedule, ReelsConfig } from '../services/types'
import { archiveReelSchedule, cancelReelSchedule, createReelSchedule, reelPages, reelsConfig, restoreReelSchedule, resumeReelSchedule, uploadReelAsset, verifyReelScheduleLink } from '../services/reel_scheduler'
import { Empty, Modal, time } from '../components/ui'
import { Pagination } from '../components/Pagination'
import { api } from '../services/api'
import { reelRequestId } from '../services/reel_request'
import { calendarFilter, calendarHeading, localDateTimePreview, reelDateTime, todayCalendarDate, type CalendarGroup } from '../services/reel_dates'
import { useServerPage, useServerRecord, watchWorkspaceUpdates, type ServerPage } from '../hooks/useServerPage'
import { ResourceSelect } from '../components/ResourceSelect'
import './reel-scheduler.css'

const labels:Record<string,string>={scheduled:'Đã đặt lịch',waiting_profile:'Chờ profile rảnh',opening:'Đang mở Chrome',switching:'Đang chuyển Page',uploading:'Đang tải lên Facebook',processing:'Facebook đang xử lý video',caption:'Đang nhập caption',review:'Đang kiểm tra nội dung',publishing:'Đang gửi đăng',verifying:'Đang xác minh Reel',completed:'Đã đăng và xác minh',needs_user:'Cần thao tác trong Chrome',needs_review:'Cần kiểm tra kết quả đăng',cancelled:'Đã hủy',interrupted:'Bị gián đoạn'}
function statusLabel(value:string){return value==='approval'?'Chờ duyệt Telegram':labels[value]||value.replaceAll('_',' ')}
function statusTone(status:string){return status==='completed'?'success':['needs_user','needs_review','interrupted'].includes(status)?'attention':status==='cancelled'?'muted':'pending'}
function size(value:number){return value>=1024**3?(value/1024**3).toFixed(1)+' GB':value>=1024**2?(value/1024**2).toFixed(1)+' MB':Math.ceil(value/1024)+' KB'}
const zonedTime=reelDateTime
function facebookLink(value:string|undefined|null){try{const url=new URL(value||'');return url.protocol==='https:'&&(url.hostname==='facebook.com'||url.hostname.endsWith('.facebook.com'))?url.href:null}catch{return null}}
function previewLink(value:string|undefined,assetId?:string){
 const match=/^\/api\/(?:v1\/)?reels\/assets\/([a-zA-Z0-9_-]+)\/video$/.exec(value||'')
 return match&&(!assetId||match[1]===assetId)?'/api/v1/reels/assets/'+match[1]+'/video':''
}
function verificationLink(value:string){try{const url=new URL(value.trim()),match=/^\/reels?\/(\d+)\/?$/.exec(url.pathname);return value.length<=2048&&url.protocol==='https:'&&['facebook.com','www.facebook.com','m.facebook.com'].includes(url.hostname)&&!url.username&&!url.password&&(!url.port||url.port==='443')&&match?'https://www.facebook.com/reel/'+match[1]:null}catch{return null}}
function canVerifyLink(job:ReelSchedule){return job.can_verify_link===true&&job.publish_attempted===true&&!job.archived_at&&['needs_review','needs_user','interrupted','cancelled'].includes(job.status)}
function postingHelp(reason:ReelPostingEligibility['posting_block_reason']|undefined){
 if(reason==='page_identity_unverified')return 'Chưa xác nhận được đúng ID Page. Kiểm tra lại Page đã chọn trước khi bật đăng.'
 if(reason==='page_policy_unavailable')return 'Chưa kiểm tra được điều kiện thông tin Page. Thử lại khi Page Manager sẵn sàng.'
 return 'Page chưa được xác nhận đã đổi thông tin. Hoàn tất cập nhật và xác minh thông tin Page trước khi bật đăng.'
}
function eligiblePage(value:ReelPostingEligibility|null|undefined){return value?.can_publish===true&&value.information_status==='updated'&&value.posting_block_reason===null&&/^[1-9][0-9]{0,31}$/.test(value.page_id||'')}
function manualHelp(job:ReelSchedule){
 if(job.archived_at||!['needs_user','needs_review','interrupted'].includes(job.status))return null
 return <p className="hint">Nếu cần xử lý trực tiếp, bấm Hủy lịch để dừng phiên Chrome ẩn nếu nút này còn hiện. Dùng Open tại Dashboard mở đúng profile; xử lý xong thì dừng Chrome rồi Tiếp tục lịch. Dấu đã gửi đăng được giữ để tránh đăng trùng.</p>
}

function SavedJobVideo({job,asset}:{job:ReelSchedule;asset:ReelAsset|undefined}){
 const source=previewLink(job.asset_preview_url,job.asset_id)||(asset?.id===job.asset_id?previewLink(asset.preview_url,job.asset_id):'')
 const [failed,setFailed]=useState(false)
 useEffect(()=>setFailed(false),[source])
 if(!source)return <div className="reels-job-preview-missing"><Film size={24}/><span>Video của lịch chưa có sẵn trong kho.</span><small>Bấm Làm mới lịch để đọc lại video.</small></div>
 return <div className="reels-job-preview"><video aria-label={'Xem video lịch Reel #'+job.id} src={source} controls playsInline preload="metadata" onError={()=>setFailed(true)} onLoadedData={()=>setFailed(false)}/>{failed&&<p role="status">Không đọc được video này. Tệp có thể đã mất hoặc trình duyệt chưa hỗ trợ định dạng.</p>}</div>
}

interface Props {notify:Notify;initialProfileId?:number;view?:'create'|'saved'}
export function ReelSchedulerPage({notify,initialProfileId,view='create'}:Props){
 const [profileId,setProfileId]=useState(initialProfileId||0)
 const {value:selectedProfile}=useServerRecord<Profile>(view==='create'&&profileId?'profiles/'+profileId:null)
 const readyProfileId=selectedProfile&&!selectedProfile.bm?profileId:0
 const [config,setConfig]=useState<ReelsConfig|null>(null),[configError,setConfigError]=useState('')
 const [pages,setPages]=useState<ReelPageOption[]>([]),[pagesLoading,setPagesLoading]=useState(false),[pagesError,setPagesError]=useState(''),[pageChoice,setPageChoice]=useState('')
 const [pageOptionPage,setPageOptionPage]=useState(1),[pageOptionTotal,setPageOptionTotal]=useState(0),[pageSearch,setPageSearch]=useState(''),[pageQuery,setPageQuery]=useState(''),[pinnedPage,setPinnedPage]=useState<ReelPageOption|null>(null)
 useEffect(()=>{const timer=window.setTimeout(()=>{setPageQuery(pageSearch.trim());setPageOptionPage(1)},250);return()=>window.clearTimeout(timer)},[pageSearch])
 const [pageRefresh,setPageRefresh]=useState(0)
 const [pageEligibility,setPageEligibility]=useState<ReelPostingEligibility|null>(null),[eligibilityLoading,setEligibilityLoading]=useState(false)
 const [eligibilityUrl,setEligibilityUrl]=useState('')
 const [manualUrl,setManualUrl]=useState(''),[manualName,setManualName]=useState('')
 const [assets,setAssets]=useState<ReelAsset[]>([]),[assetsError,setAssetsError]=useState(''),[assetId,setAssetId]=useState<string|null>(null),[file,setFile]=useState<File|null>(null),[localPreview,setLocalPreview]=useState('')
 const [uploading,setUploading]=useState(false),[uploadProgress,setUploadProgress]=useState(0),[uploadError,setUploadError]=useState('')
 const uploadController=useRef<AbortController|null>(null),fileInput=useRef<HTMLInputElement|null>(null)
 const [caption,setCaption]=useState(''),[scheduledLocal,setScheduledLocal]=useState(''),[timezone,setTimezone]=useState(''),[fold,setFold]=useState('')
 const [saving,setSaving]=useState(false),[formError,setFormError]=useState(''),[savedMessage,setSavedMessage]=useState('')
 const [pending,setPending]=useState(new Set<number>())
 const assetPageSize=25
 const [assetPage,setAssetPage]=useState(1),[assetSearch,setAssetSearch]=useState(''),[assetTotal,setAssetTotal]=useState(0),[pinnedAsset,setPinnedAsset]=useState<ReelAsset|null>(null)
 const [assetQuery,setAssetQuery]=useState('')
 useEffect(()=>{const timer=window.setTimeout(()=>setAssetQuery(assetSearch.trim()),250);return()=>window.clearTimeout(timer)},[assetSearch])
 const [verificationDrafts,setVerificationDrafts]=useState<Record<number,string>>({}),[verificationErrors,setVerificationErrors]=useState<Record<number,string>>({})
 const [search,setSearch]=useState(''),[status,setStatus]=useState('all'),[page,setPage]=useState(1),[pageSize,setPageSize]=useState(10),[showArchived,setShowArchived]=useState(false)
 const [detailJobId,setDetailJobId]=useState<number|null>(null)
 const [calendarGroup,setCalendarGroup]=useState<CalendarGroup>('day')
 const [calendarMode,setCalendarMode]=useState<'today'|'all'|CalendarGroup>('today'),[calendarValue,setCalendarValue]=useState('')
 const [calendarToday,setCalendarToday]=useState(()=>todayCalendarDate('America/Los_Angeles')),[calendarReady,setCalendarReady]=useState(false)
 const [scheduleSort,setScheduleSort]=useState('recent')
 const calendarZone=config?.default_timezone||'America/Los_Angeles'
 const calendarQuery=calendarMode==='today'?calendarFilter(calendarToday,'day'):calendarFilter(calendarValue,calendarMode)
 const calendarInvalid=calendarMode!=='all'&&!calendarQuery
 const jobsData=useServerPage<ReelSchedule>('reels',{q:search.trim(),status,include_archived:showArchived,calendar:calendarQuery,sort:scheduleSort},page,pageSize,setPage,view==='saved'&&calendarReady&&!calendarInvalid)
 const jobs=jobsData.items,jobsLoading=jobsData.loading,jobsError=jobsData.error,refresh=jobsData.refresh
 const {value:detailJob,refresh:refreshDetail}=useServerRecord<ReelSchedule>(detailJobId===null?null:'reels/'+detailJobId,jobs.find(job=>job.id===detailJobId)||null)
 useEffect(()=>{if(view!=='saved')setDetailJobId(null)},[view])
 const assetsRequest=useRef(0),assetReadController=useRef<AbortController|null>(null),pagesProfile=useRef<number|null>(null),pagesRefreshVersion=useRef(-1),pagesReadBusy=useRef(false),pagesCatalogKey=useRef('')
 const currentPageChoice=useRef(pageChoice)
 currentPageChoice.current=pageChoice
 const refreshAssets=useCallback(async()=>{const request=++assetsRequest.current;assetReadController.current?.abort();const controller=new AbortController();assetReadController.current=controller;try{const result=await api<ServerPage<ReelAsset>>('/reel-assets?limit='+assetPageSize+'&offset='+((assetPage-1)*assetPageSize)+'&q='+encodeURIComponent(assetQuery),'GET',undefined,controller.signal);if(request===assetsRequest.current){setAssets(result.items);setAssetTotal(result.total);const last=Math.max(1,Math.ceil(result.total/assetPageSize));if(assetPage>last)setAssetPage(last);setAssetsError('')}}catch(reason){if(request===assetsRequest.current&&(reason as Error).name!=='AbortError')setAssetsError((reason as Error).message)}},[assetPage,assetPageSize,assetQuery])
 async function refreshSaved(){await Promise.all([refresh(),refreshDetail()])}
 useEffect(()=>{
  let live=true
  void reelsConfig().then(settings=>{if(live){setConfig(settings);setTimezone(current=>current||settings.default_timezone);setCalendarToday(todayCalendarDate(settings.default_timezone||'America/Los_Angeles'));setCalendarReady(true)}}).catch(reason=>{if(live){setConfigError((reason as Error).message);setCalendarToday(todayCalendarDate('America/Los_Angeles'));setCalendarReady(true)}})
  return ()=>{live=false;assetsRequest.current++;assetReadController.current?.abort();uploadController.current?.abort()}
 },[])
 useEffect(()=>{
  if(calendarMode!=='today'||!calendarReady)return
  const update=()=>setCalendarToday(todayCalendarDate(calendarZone))
  update();const timer=window.setInterval(update,1000)
  window.addEventListener('focus',update);document.addEventListener('visibilitychange',update)
  return()=>{window.clearInterval(timer);window.removeEventListener('focus',update);document.removeEventListener('visibilitychange',update)}
 },[calendarMode,calendarReady,calendarZone])
 useEffect(()=>{if(view==='create'&&assetSearch.trim()===assetQuery)void refreshAssets();return()=>{assetsRequest.current++;assetReadController.current?.abort()}},[refreshAssets,view,assetSearch,assetQuery])

 useEffect(()=>{
  if(view!=='create'||!readyProfileId){pagesReadBusy.current=false;setPagesLoading(false);return}
  const catalogKey=readyProfileId+'|'+pageOptionPage+'|'+pageQuery
  if(pagesCatalogKey.current===catalogKey&&pagesRefreshVersion.current===pageRefresh)return
  let live=true,settled=false
  const controller=new AbortController(),sameCatalog=pagesCatalogKey.current===catalogKey
  const changedProfile=pagesProfile.current!==readyProfileId
  if(changedProfile){setPages([]);setPinnedPage(null);setPageChoice('');setManualUrl('');setManualName('');setPageOptionPage(1);setPageSearch('');if(pageQuery||pageOptionPage!==1){setPageQuery('');return}}
  pagesProfile.current=readyProfileId;pagesRefreshVersion.current=pageRefresh;pagesCatalogKey.current=catalogKey
  setPagesError('')
  pagesReadBusy.current=true;setPagesLoading(true)
  void reelPages(readyProfileId,pageOptionPage,pageQuery,controller.signal).then(result=>{if(live){
   const items=result.items
   setPageOptionTotal(result.total)
   const last=Math.max(1,Math.ceil(result.total/25));if(pageOptionPage>last)setPageOptionPage(last)
   const selected=currentPageChoice.current,eligible=items.filter(eligiblePage)
   setPages(items)
   const chosen=items.find(item=>item.page_url===selected)
   if(chosen)setPinnedPage(chosen)
   if(changedProfile||!selected){setPageChoice(eligible[0]?.page_url||'manual');setPinnedPage(eligible[0]||null)}
   else if(selected!=='manual'&&(chosen&&!eligiblePage(chosen)||sameCatalog&&pages.some(item=>item.page_url===selected)&&!chosen)){
    // A target omitted from the eligible catalog must not reappear as an
    // automatically filled manual target. Explicit manual drafts stay separate.
    setManualUrl('');setManualName('');setPageChoice(eligible[0]?.page_url||'manual');setPinnedPage(eligible[0]||null)
   }
  }}).catch(reason=>{if(live){setPagesError((reason as Error).message);if(changedProfile)setPageChoice('manual')}}).finally(()=>{if(live){settled=true;pagesReadBusy.current=false;setPagesLoading(false)}})
  return ()=>{live=false;controller.abort();pagesReadBusy.current=false;if(!settled)pagesRefreshVersion.current=-1}
 },[readyProfileId,view,pageRefresh,pageOptionPage,pageQuery])
 function refreshPages(){if(view==='create'&&readyProfileId&&!saving&&!uploading&&!pagesReadBusy.current){pagesReadBusy.current=true;setPageRefresh(value=>value+1)}}
 useEffect(()=>{if(!file){setLocalPreview('');return}const url=URL.createObjectURL(file);setLocalPreview(url);return ()=>URL.revokeObjectURL(url)},[file])
 useEffect(()=>setPage(1),[search,status,pageSize,showArchived,calendarQuery,scheduleSort])
 const selectedPage=readyProfileId&&pagesProfile.current===readyProfileId?(pages.find(item=>item.page_url===pageChoice)||(pinnedPage?.page_url===pageChoice?pinnedPage:null)):null,selectedAsset=assets.find(asset=>asset.id===assetId)||(pinnedAsset?.id===assetId?pinnedAsset:null)
 const pageUrl=pageChoice==='manual'?manualUrl.trim():selectedPage?.page_url||'',pageName=pageChoice==='manual'?manualName.trim():selectedPage?.page_name||''
 const videoSource=localPreview||(selectedAsset?previewLink(selectedAsset.preview_url,selectedAsset.id):'')
 const assetsById=useMemo(()=>new Map(assets.map(asset=>[asset.id,asset])),[assets])
 useEffect(()=>{
  let live=true
  setPageEligibility(null);setEligibilityUrl('')
  if(view!=='create'||!facebookLink(pageUrl)){setEligibilityLoading(false);return}
  setEligibilityLoading(true)
  let sequence=0
  const refreshEligibility=async()=>{const request=++sequence;try{const result=await api<ReelPostingEligibility>('/reels/page-eligibility?page_url='+encodeURIComponent(pageUrl));if(live&&request===sequence){setPageEligibility(result);setEligibilityUrl(pageUrl);if(!eligiblePage(result)&&currentPageChoice.current===pageUrl){setPages(items=>items.map(item=>item.page_url===pageUrl?{...item,...result}:item));setPinnedPage(null);setPageChoice('manual');setManualUrl('');setManualName('')}}}catch{if(live&&request===sequence){setPageEligibility({page_id:null,can_publish:false,posting_block_reason:'page_policy_unavailable'});setEligibilityUrl(pageUrl)}}finally{if(live&&request===sequence)setEligibilityLoading(false)}}
  void refreshEligibility()
  const stop=watchWorkspaceUpdates(refreshEligibility,'fallback')
  return ()=>{live=false;stop()}
 },[pageUrl,view,pageRefresh])
 const canSchedulePage=!!readyProfileId&&eligibilityUrl===pageUrl&&eligiblePage(pageEligibility)&&!eligibilityLoading
 function chooseFile(value:File|undefined){
  setAssetId(null);setFile(null);setUploadError('');setUploadProgress(0);setSavedMessage('')
  if(!value)return
  const extensions=config?.accepted_extensions||['.mp4','.m4v','.mov']
  if(!extensions.some(extension=>value.name.toLowerCase().endsWith(extension.toLowerCase()))){setUploadError('Chọn video '+extensions.join(', ')+'.');if(fileInput.current)fileInput.current.value='';return}
  if(!value.size||value.size>(config?.max_upload_bytes||0)){setUploadError('Video phải có dữ liệu và không quá '+size(config?.max_upload_bytes||0)+'.');if(fileInput.current)fileInput.current.value='';return}
  setFile(value)
 }
 async function upload(){
  if(!file)return
  assetsRequest.current++
  const controller=new AbortController();uploadController.current=controller
  setUploading(true);setUploadError('');setUploadProgress(0)
  try{
   const asset=await uploadReelAsset(file,(loaded,total)=>setUploadProgress(total?Math.min(100,Math.round(loaded/total*100)):0),controller.signal)
   assetsRequest.current++;setAssets(old=>[asset,...old.filter(item=>item.id!==asset.id)].slice(0,assetPageSize));setAssetId(asset.id);setPinnedAsset(asset);setFile(null);setUploadProgress(100)
   if(fileInput.current)fileInput.current.value=''
   notify('Đã lưu video vào Manager. Chưa tải lên Facebook.');await refreshAssets()
  }catch(reason){setUploadError((reason as Error).message)}finally{setUploading(false);uploadController.current=null}
 }
 async function schedule(event:FormEvent){
  event.preventDefault();setFormError('');setSavedMessage('')
  if(!facebookLink(pageUrl)){setFormError('Nhập link Page Facebook HTTPS hợp lệ.');return}
  if(!canSchedulePage){setFormError(postingHelp(pageEligibility?.posting_block_reason));return}
  if(!selectedAsset){setFormError('Tải video lên Manager hoặc chọn video đã lưu trước khi đặt lịch.');return}
  setSaving(true)
  try{
   const payload={profile_id:profileId,page_url:pageUrl,...(pageName?{page_name:pageName}:{}),asset_id:selectedAsset.id,caption,scheduled_local:scheduledLocal,timezone:timezone.trim(),...(fold!==''?{fold:Number(fold) as 0|1}:{})}
   await createReelSchedule({...payload,request_id:await reelRequestId(payload)})
   setSavedMessage('Đã lưu lịch. Đến giờ, Manager sẽ chạy Chrome ngầm bằng đúng profile và đăng Reel lên Page đã chọn.');notify('Đã lưu lịch đăng Reel');await refresh()
  }catch(reason){setFormError((reason as Error).message)}finally{setSaving(false)}
 }
 async function action(job:ReelSchedule,kind:'cancel'|'resume'|'archive'|'restore'){
  setPending(old=>new Set(old).add(job.id))
  try{if(kind==='cancel')await cancelReelSchedule(job.id);else if(kind==='archive')await archiveReelSchedule(job.id);else if(kind==='restore')await restoreReelSchedule(job.id);else await resumeReelSchedule(job.id);notify(kind==='cancel'?'Đã yêu cầu hủy lịch Reel':kind==='archive'?'Đã ẩn lịch đã hủy; video được giữ lại.':kind==='restore'?'Đã khôi phục lịch ở trạng thái đã hủy. Lịch chưa chạy lại.':'Đã yêu cầu tiếp tục lịch Reel');await refreshSaved()}
  catch(reason){notify((reason as Error).message,true)}finally{setPending(old=>{const next=new Set(old);next.delete(job.id);return next})}
 }
 async function verifyLink(event:FormEvent,job:ReelSchedule){
  event.preventDefault()
  if(!canVerifyLink(job)||pending.has(job.id))return
  const target=verificationLink(verificationDrafts[job.id]??job.verification_url??'')
  if(!target){setVerificationErrors(old=>({...old,[job.id]:'Dùng link Reel Facebook HTTPS có ID số, ví dụ https://www.facebook.com/reel/123456.'}));return}
  setVerificationErrors(old=>({...old,[job.id]:''}));setPending(old=>new Set(old).add(job.id))
  try{
   await verifyReelScheduleLink(job.id,target)
   setVerificationDrafts(old=>({...old,[job.id]:target}));notify('Đã yêu cầu kiểm tra video tại link Reel đã nhập.');await refreshSaved()
  }catch(reason){setVerificationErrors(old=>({...old,[job.id]:(reason as Error).message}))}
  finally{setPending(old=>{const next=new Set(old);next.delete(job.id);return next})}
 }
 function renderVerification(job:ReelSchedule){
  if(!canVerifyLink(job))return null
  return <form className="reels-form" aria-label={'Xác minh Reel đã đăng #'+job.id} onSubmit={event=>void verifyLink(event,job)}>
   <label>Link Reel đã đăng<input type="url" aria-label="Link Reel đã đăng" required maxLength={2048} value={verificationDrafts[job.id]??job.verification_url??''} disabled={pending.has(job.id)} onChange={event=>{const value=event.target.value;setVerificationDrafts(old=>({...old,[job.id]:value}));setVerificationErrors(old=>({...old,[job.id]:''}))}} placeholder="https://www.facebook.com/reel/123456"/></label>
   <p className="hint">Chỉ kiểm tra video tại link Reel đã nhập. Không đăng thêm video.</p>
   {verificationErrors[job.id]&&<p className="reels-error" role="alert">{verificationErrors[job.id]}</p>}
   <button className="button small" type="submit" aria-label={'Xác minh link Reel #'+job.id} disabled={pending.has(job.id)}>Xác minh link Reel</button>
  </form>
 }
 function renderJobDetails(job:ReelSchedule){
  return <div className="reels-job-details" aria-label={'Chi tiết lịch Reel #'+job.id}><div className="reels-job-heading"><p>{job.profile_name} · Lịch #{job.id}</p><span className={'reels-status '+statusTone(job.archived_at?'cancelled':job.status)}>{job.archived_at?'Đã xóa khỏi danh sách':statusLabel(job.status)}</span></div><div className="reels-job-time"><CalendarClock size={16}/><div><strong>{zonedTime(job.scheduled_at,job.timezone)}</strong><small>{job.timezone}</small></div></div><div className="reels-job-video"><Film size={14}/><span>{job.asset_name}</span><small>{size(job.asset_size)}</small></div>{job.caption&&<details className="reels-job-caption"><summary>Caption · {job.caption.length.toLocaleString()} ký tự</summary><p>{job.caption}</p></details>}<div className="reels-job-step"><strong>{statusLabel(job.step)}</strong>{job.message&&<p>{job.message}</p>}</div>{job.status==='needs_review'&&<p className="reels-review-warning">Chưa thấy video sau khi gửi đăng. Mở link Reel để kiểm tra; hệ thống không tự đăng lại để tránh trùng Reel.</p>}{renderVerification(job)}{manualHelp(job)}<div className="reels-job-actions">{facebookLink(job.published_url)&&<a className="button small" href={facebookLink(job.published_url)!} target="_blank" rel="noreferrer">Mở Reel đã đăng</a>}{job.can_cancel&&<button className="button small" type="button" aria-label={'Hủy lịch Reel #'+job.id} disabled={pending.has(job.id)} onClick={()=>void action(job,'cancel')}>Hủy lịch</button>}{job.can_resume&&<button className="button small" type="button" aria-label={'Tiếp tục lịch Reel #'+job.id} disabled={pending.has(job.id)} onClick={()=>void action(job,'resume')}>Tiếp tục</button>}{job.can_archive&&<button className="button small" type="button" aria-label={'Xóa lịch Reel #'+job.id} title="Ẩn lịch đã hủy khỏi danh sách; video được giữ lại." disabled={pending.has(job.id)} onClick={()=>void action(job,'archive')}>Xóa lịch đã hủy</button>}{job.can_restore&&<button className="button small" type="button" aria-label={'Khôi phục lịch Reel #'+job.id} title="Khôi phục về trạng thái đã hủy, không tự chạy lại." disabled={pending.has(job.id)} onClick={()=>void action(job,'restore')}>Khôi phục</button>}</div>{job.archived_at&&<p className="hint">Lịch đã ẩn, video được giữ lại. Khôi phục chỉ đưa lịch về trạng thái đã hủy, không tự chạy lại.</p>}{job.can_archive&&<p className="hint">Ẩn lịch đã hủy khỏi danh sách; video được giữ lại.</p>}<p className="reels-job-updated">Cập nhật: {time(job.updated_at)}</p></div>
 }
 function renderJobCard(job:ReelSchedule){
  return <article className="panel reels-job reels-job-card" key={job.id} aria-label={'Lịch Reel #'+job.id} onClick={event=>{const target=event.target as HTMLElement;if(!target.closest('video,button,a,input,textarea,select,summary,details'))setDetailJobId(job.id)}}><button className="reels-card-page" type="button" aria-label={'Xem chi tiết lịch Reel #'+job.id} onClick={()=>setDetailJobId(job.id)}>{job.page_name||job.page_url}</button><p className="reels-card-time"><CalendarClock size={15}/><time dateTime={job.scheduled_at}>{zonedTime(job.scheduled_at,job.timezone)}</time><small>{job.timezone}</small></p><p className="reels-card-caption">{job.caption||'(Không có caption)'}</p><SavedJobVideo job={job} asset={assetsById.get(job.asset_id)}/></article>
 }
 const current=page,visible=calendarInvalid?[]:jobs
 const calendarGroups:{key:string;label:string;zone:string;items:ReelSchedule[]}[]=[],combinedGroups=new Map<string,typeof calendarGroups[number]>()
 const occurrences=new Map<string,number>()
 for(const job of visible){
  const heading=calendarHeading(job.scheduled_at,job.timezone,calendarGroup),key=heading.key+'|'+job.timezone
  let group=scheduleSort==='recent'?combinedGroups.get(key):calendarGroups.at(-1)
  if(!group||!group.key.startsWith(key+'#')){
   const occurrence=occurrences.get(key)||0;occurrences.set(key,occurrence+1)
   group={key:key+'#'+occurrence,label:heading.label,zone:job.timezone,items:[]};calendarGroups.push(group);combinedGroups.set(key,group)
  }
  group.items.push(job)
 }
 return <div className={'reels-page '+(view==='saved'?'reels-saved-view':'reels-create-view')}>
  {view==='create'&&<>
  {configError&&<p className="reels-error" role="alert">{configError}</p>}
  {config&&!config.background_browser&&<p className="reels-error" role="status">Máy chủ chưa nạp chế độ Reel chạy ngầm. Mở lại server để áp dụng.</p>}
  {config&&!config.scheduler_running&&<p className="reels-error" role="status">Bộ đặt lịch chưa chạy. Lịch chỉ được thực hiện khi server và bộ đặt lịch hoạt động.</p>}
  <section className="reels-form-panel reels-create-layout">
   {<form className="reels-form" onSubmit={schedule}>
    <div className="reels-two"><label>Chrome profile<ResourceSelect<Profile> path="profile-options" params={{scope:'regular'}} label="Profile đăng Reel" required value={profileId} selected={selectedProfile} disabled={saving||uploading} onChange={setProfileId} display={profile=>profile.name+(profile.facebook?.name?' · '+profile.facebook.name:'')} autoSelect/></label>
    <label>Page đăng Reel<div className="resource-select"><input aria-label="Tìm Page đăng Reel" placeholder="Tìm Page trong profile đã chọn…" value={pageSearch} disabled={saving||uploading} onChange={event=>setPageSearch(event.target.value)}/><select aria-label="Page đăng Reel" required value={pageChoice} disabled={saving||uploading||pagesLoading||!readyProfileId} onChange={event=>{const value=event.target.value;setPageChoice(value);setPinnedPage(pages.find(item=>item.page_url===value)||null)}}>{pagesLoading&&<option value="">Đang đọc Page đã lưu…</option>}{pinnedPage&&pageChoice===pinnedPage.page_url&&!pages.some(item=>item.page_url===pageChoice)&&<option value={pinnedPage.page_url}>{pinnedPage.page_name||pinnedPage.page_url}</option>}{pages.map(item=><option key={item.page_url} value={item.page_url} disabled={!eligiblePage(item)}>{item.page_name||item.page_url} · {item.source==='received'?'Page đã nhận':'Danh sách đã lưu'} · {item.can_publish===true?'Đã đổi thông tin':'Chưa đủ điều kiện đăng'}</option>)}<option value="manual">Nhập link Page khác</option></select><div className="resource-select-pages" role="group" aria-label="Phân trang Page đăng Reel"><small>{pageOptionTotal?((pageOptionPage-1)*25+1):0}–{Math.min(pageOptionPage*25,pageOptionTotal)} / {pageOptionTotal}</small>{pageOptionTotal>25&&<><button className="button small" type="button" aria-label="Trang trước Page đăng Reel" disabled={saving||uploading||pagesLoading||pageOptionPage===1} onClick={()=>setPageOptionPage(value=>value-1)}>Trước</button><span>{pageOptionPage} / {Math.ceil(pageOptionTotal/25)}</span><button className="button small" type="button" aria-label="Trang sau Page đăng Reel" disabled={saving||uploading||pagesLoading||pageOptionPage>=Math.ceil(pageOptionTotal/25)} onClick={()=>setPageOptionPage(value=>value+1)}>Sau</button></>}</div></div></label></div>
    <div><button className="button small" type="button" disabled={!profileId||saving||uploading||pagesLoading} onClick={refreshPages}><RefreshCw size={14}/>{pagesLoading?'Đang đọc Page…':'Làm mới Page'}</button></div>
    <p className="hint">Dùng Page đã lưu hoặc Page đã nhận của Chrome profile đã chọn. Đến giờ, Manager chạy Chrome ngầm bằng đúng profile và proxy để đăng Reel.</p>
    {pageUrl&&<p className={canSchedulePage?'hint':'reels-error'} role="status">{!facebookLink(pageUrl)?'Nhập link Page Facebook HTTPS hợp lệ.':eligibilityLoading?'Đang kiểm tra điều kiện thông tin Page…':canSchedulePage?'Page đã được xác nhận cập nhật thông tin.':postingHelp(pageEligibility?.posting_block_reason)}</p>}
    {!pagesLoading&&!pages.length&&<p className="hint">Chưa có Page đủ điều kiện trong danh sách đã đọc. Làm mới Page sau khi xác minh thông tin Page.</p>}{pagesError&&<p className="hint">Không đọc được danh sách Page. Bạn có thể nhập link thủ công: {pagesError}</p>}
    {pageChoice==='manual'&&<div className="reels-two"><label>Link Page<input aria-label="Link Page thủ công" type="url" required value={manualUrl} disabled={saving} onChange={event=>setManualUrl(event.target.value)} placeholder="https://www.facebook.com/…"/></label><label>Tên Page (tùy chọn)<input aria-label="Tên Page thủ công" value={manualName} disabled={saving} onChange={event=>setManualName(event.target.value)} placeholder="Tên để nhận diện trong lịch"/></label></div>}
    <section className="reels-video-section"><h3>Video của lịch</h3><div className="reels-two reels-video-choices"><div className="reels-asset-choices">
     <label>Tìm video đã lưu<input type="search" aria-label="Tìm video đã lưu" value={assetSearch} onChange={event=>{setAssetSearch(event.target.value);setAssetPage(1)}} placeholder="Tìm tên video…"/></label>
     <label>Video đã có<select aria-label="Video đã lưu" value={assetId||''} disabled={saving||uploading} onChange={event=>{setAssetId(event.target.value||null);setPinnedAsset(assets.find(asset=>asset.id===event.target.value)||null);setFile(null);setUploadError('');if(fileInput.current)fileInput.current.value=''}}><option value="">Chọn video trong Manager</option>{assets.map(asset=><option key={asset.id} value={asset.id}>{asset.name} · {size(asset.size)}</option>)}{pinnedAsset&&!assets.some(asset=>asset.id===pinnedAsset.id)&&<option value={pinnedAsset.id}>{pinnedAsset.name}</option>}</select></label>
     <nav className="reels-options-pager" aria-label="Phân trang video chọn lịch"><small>{assetTotal?(assetPage-1)*assetPageSize+1:0}–{Math.min(assetPage*assetPageSize,assetTotal)} / {assetTotal}</small><button className="button small" type="button" aria-label="Trang trước · Video chọn lịch" disabled={saving||uploading||assetPage===1} onClick={()=>setAssetPage(value=>value-1)}>Trước</button><button className="button small" type="button" aria-label="Trang sau · Video chọn lịch" disabled={saving||uploading||assetPage*assetPageSize>=assetTotal} onClick={()=>setAssetPage(value=>value+1)}>Sau</button></nav>
     </div><label className="reels-upload-field">Hoặc chọn tệp mới<input ref={fileInput} type="file" aria-label="Chọn video Reel" accept={(config?.accepted_extensions||['.mp4','.m4v','.mov']).join(',')} disabled={!config||saving||uploading} onChange={event=>chooseFile(event.target.files?.[0])}/></label></div>
     <p className="hint">{config?config.accepted_extensions.join(', ')+' · tối đa '+size(config.max_upload_bytes)+'. Chọn tệp chưa tự tải lên hoặc tạo lịch.':'Đang đọc giới hạn video…'}</p>
     {file&&<div className="reels-upload-controls"><span>{size(file.size)}</span><button className="button" type="button" disabled={uploading||saving} onClick={()=>void upload()}><Upload size={14}/>Tải video lên Manager</button></div>}
     {uploading&&<div className="reels-upload-progress"><progress aria-label="Tiến trình tải video lên" max={100} value={uploadProgress}/><span>{uploadProgress===100?'Đang lưu video…':uploadProgress+'%'}</span><button className="text-button" type="button" onClick={()=>uploadController.current?.abort()}><X size={13}/>Hủy tải video</button></div>}
     {uploadError&&<p className="reels-error" role="alert">{uploadError}</p>}
     {assetsError&&<p className="reels-error" role="status">Chưa đọc được kho video: {assetsError}</p>}
     {videoSource&&<video className="reels-preview-video" aria-label="Xem trước video Reel" src={videoSource} controls preload="metadata" playsInline/>}
     <small className="hint">Tải lên đây chỉ lưu video vào Manager. Facebook chỉ nhận video khi tác vụ chạy đến giờ đăng.</small>
    </section>
    <label>Caption<textarea aria-label="Caption Reel" rows={4} maxLength={5000} value={caption} disabled={saving} onChange={event=>setCaption(event.target.value)} placeholder="Nhập nội dung caption cho Reel"/><small className="hint">{caption.length.toLocaleString()} / 5.000 ký tự · Nội dung được giữ đúng như bạn nhập.</small></label>
    <div className="reels-two"><label>Ngày và giờ đăng · ngày/tháng/năm<input lang="vi" aria-label="Ngày giờ đăng Reel" type="datetime-local" required value={scheduledLocal} disabled={saving} onChange={event=>setScheduledLocal(event.target.value)}/></label><label>Múi giờ<input aria-label="Múi giờ đăng Reel" list="reel-timezones" required value={timezone} disabled={saving} onChange={event=>setTimezone(event.target.value)}/><datalist id="reel-timezones">{['America/Los_Angeles','Asia/Ho_Chi_Minh','America/New_York','UTC'].map(zone=><option key={zone} value={zone}/>)}</datalist></label></div>
    <details className="reels-dst"><summary>Giờ trùng khi đổi giờ mùa (DST)</summary><label>Lần xuất hiện của giờ đăng<select aria-label="Lần giờ DST" value={fold} disabled={saving} onChange={event=>setFold(event.target.value)}><option value="">Để server xác định</option><option value="0">Lần đầu</option><option value="1">Lần thứ hai</option></select><small className="hint">Chỉ chọn lần đầu hoặc lần thứ hai khi server báo giờ bị trùng. Giờ không tồn tại phải được đổi sang một giờ hợp lệ.</small></label></details>
    <div className="reels-review" aria-label="Xem lại lịch Reel"><strong>Kiểm tra trước khi lưu lịch</strong><dl><dt>Chrome</dt><dd>{selectedProfile?.name||'Chưa chọn'}</dd><dt>Page</dt><dd>{pageName||pageUrl||'Chưa chọn'}</dd><dt>Video</dt><dd>{selectedAsset?selectedAsset.name:'Chưa tải hoặc chọn video đã lưu'}</dd><dt>Giờ đăng</dt><dd>{scheduledLocal?localDateTimePreview(scheduledLocal)+' · '+timezone:'Chưa chọn ngày giờ'}</dd></dl><p>Thời gian trên được hiểu theo múi giờ đã chọn. Hiển thị ngày/tháng/năm đầy đủ và giờ 24 tiếng. Server kiểm tra và lưu thời điểm UTC tương ứng.</p></div>
    <p className="hint">Giữ máy và server đang chạy. Khi mở lại server, lịch quá giờ sẽ được đưa vào xử lý.</p>
    {formError&&<p className="reels-error" role="alert">{formError}</p>}{savedMessage&&<p className="reels-saved" role="status"><CheckCircle2 size={15}/>{savedMessage}</p>}
    <button className="button primary reels-schedule" type="submit" disabled={saving||uploading||!selectedAsset||!profileId||!pageUrl||!config||!canSchedulePage}><CalendarClock size={15}/>{saving?'Đang lưu lịch…':'Đặt lịch đăng Reel'}</button>
   </form>}
  </section></>}
  {view==='saved'&&<section className="reels-jobs" aria-label="Danh sách lịch Reel"><div className="reels-filters"><div className="search"><Search size={15}/><input aria-label="Tìm lịch Reel" value={search} onChange={event=>setSearch(event.target.value)} placeholder="Tìm profile, Page, video…"/></div><select aria-label="Lọc trạng thái lịch Reel" value={status} onChange={event=>setStatus(event.target.value)}><option value="all">Tất cả trạng thái</option>{Object.entries(labels).map(([key,value])=><option key={key} value={key}>{value}</option>)}</select><label className="reels-calendar-choice">Lọc lịch theo<select aria-label="Lọc thời gian lịch Reel" value={calendarMode} onChange={event=>{setCalendarMode(event.target.value as 'today'|'all'|CalendarGroup);setCalendarValue('');if(event.target.value==='today')setCalendarToday(todayCalendarDate(calendarZone))}}><option value="today">Hôm nay</option><option value="all">Tất cả ngày</option><option value="day">Ngày</option><option value="month">Tháng</option><option value="year">Năm</option></select></label>{calendarMode!=='all'&&calendarMode!=='today'&&<label className="reels-calendar-choice">{calendarMode==='day'?'Ngày đăng':calendarMode==='month'?'Tháng đăng':'Năm đăng'}<input lang="vi" aria-label="Giá trị thời gian lịch Reel" type={calendarMode==='day'?'date':calendarMode==='month'?'month':'number'} min={calendarMode==='year'?'1000':undefined} max={calendarMode==='year'?'9999':undefined} step={calendarMode==='year'?1:undefined} placeholder={calendarMode==='year'?'yyyy':undefined} value={calendarValue} onChange={event=>setCalendarValue(event.target.value)}/></label>}<label className="reels-calendar-choice">Thứ tự<select aria-label="Thứ tự lịch Reel" value={scheduleSort} onChange={event=>setScheduleSort(event.target.value)}><option value="recent">Mới tạo gần nhất</option><option value="scheduled_asc">Giờ đăng tăng dần</option><option value="scheduled_desc">Giờ đăng giảm dần</option></select></label><label className="reels-calendar-choice">Nhóm lịch trên trang<select aria-label="Nhóm lịch Reel trên trang" value={calendarGroup} onChange={event=>setCalendarGroup(event.target.value as CalendarGroup)}><option value="day">Theo ngày</option><option value="month">Theo tháng</option><option value="year">Theo năm</option></select></label><label className="check-label"><input type="checkbox" checked={showArchived} onChange={event=>setShowArchived(event.target.checked)}/>Hiện lịch đã xóa</label><button className="button small" type="button" onClick={()=>void refreshSaved()}><RefreshCw size={13}/>Làm mới lịch</button></div>
   {calendarMode==='today'&&calendarReady&&<p className="hint reels-calendar-heading">Hôm nay: {calendarToday.split('-').reverse().join('/')} · {calendarZone}</p>}{jobsError&&<p className="reels-error" role="alert">{jobsError}</p>}
   {assetsError&&<p className="reels-error" role="status">Chưa đọc được kho video: {assetsError}</p>}
   {!calendarReady?<p className="loading">Đang xác định ngày hôm nay…</p>:calendarInvalid?<section className="panel reels-empty" role="status"><Empty title="Chưa áp dụng bộ lọc thời gian" detail="Nhập kỳ hợp lệ để xem lịch: năm đủ 4 chữ số từ 1000 đến 9999, hoặc chọn ngày / tháng hợp lệ."/></section>:jobsLoading&&!jobs.length?<p className="loading">Đang tải lịch…</p>:!visible.length?<section className="panel reels-empty"><Empty title={calendarMode!=='all'?'Không có lịch trong kỳ đã chọn':jobs.length?'Không có lịch phù hợp':'Chưa có lịch đăng Reel'} detail={calendarMode!=='all'?'Chọn Tất cả ngày hoặc một kỳ khác để xem lịch đã lưu.':jobs.length?'Thử đổi từ khóa hoặc trạng thái.':'Lịch được lưu qua lần mở lại ứng dụng và thực hiện khi server đang chạy.'}/></section>:calendarGroups.flatMap(group=>[<h3 className="reels-calendar-heading" key={'heading:'+group.key}>{calendarGroup==='day'?'Ngày ':calendarGroup==='month'?'Tháng ':'Năm '}{group.label}<small>{group.zone}</small></h3>,...group.items.map(renderJobCard)])}
   <Pagination total={calendarInvalid?0:jobsData.total} page={current} pageSize={pageSize} label="lịch Reel" onPageChange={setPage} onPageSizeChange={value=>{setPageSize(value);setPage(1)}}/>
  </section>}
  {view==='saved'&&detailJob&&<Modal title={detailJob.page_name||detailJob.page_url} close={()=>setDetailJobId(null)}>{renderJobDetails(detailJob)}</Modal>}
 </div>
}
