import { api, ensureChromeSession } from './api'
import { identityHeaders, identityRedirect } from './identity'
import type { ReelAsset, ReelPageOption, ReelScheduleInput, ReelsConfig } from './types'
import type { ServerPage } from '../hooks/useServerPage'

export const reelsConfig=()=>api<ReelsConfig>('/reels/config')
export const reelPages=(profileId:number,page=1,q='',signal?:AbortSignal)=>api<ServerPage<ReelPageOption>>('/reel-page-options?profile_id='+profileId+'&limit=25&offset='+((page-1)*25)+'&q='+encodeURIComponent(q),'GET',undefined,signal)
export const createReelSchedule=(body:ReelScheduleInput)=>api('/reels','POST',body)
export const cancelReelSchedule=(id:number)=>api('/reels/'+id+'/cancel','POST')
export const resumeReelSchedule=(id:number)=>api('/reels/'+id+'/resume','POST')
export const verifyReelScheduleLink=(id:number,reelUrl:string)=>api('/reels/'+id+'/verify-link','POST',{reel_url:reelUrl})
export const archiveReelSchedule=(id:number)=>api('/reels/'+id+'/archive','POST')
export const restoreReelSchedule=(id:number)=>api('/reels/'+id+'/restore','POST')

export async function uploadReelAsset(file:File,onProgress:(loaded:number,total:number)=>void,signal:AbortSignal):Promise<ReelAsset>{
 await ensureChromeSession()
 const identity=await identityHeaders()
 return new Promise((resolve,reject)=>{
  if(signal.aborted){reject(new Error('Đã hủy tải video lên.'));return}
  const xhr=new XMLHttpRequest()
  const abort=()=>xhr.abort()
  const cleanup=()=>signal.removeEventListener('abort',abort)
  xhr.open('POST','/api/v1/reels/assets');xhr.withCredentials=true
  xhr.setRequestHeader('Content-Type','video/mp4');xhr.setRequestHeader('X-Filename',encodeURIComponent(file.name))
  for(const [name,value] of Object.entries(identity))xhr.setRequestHeader(name,value)
  xhr.upload.onprogress=event=>onProgress(event.loaded,event.lengthComputable?event.total:file.size)
  xhr.onload=()=>{
   cleanup()
   try{
    const payload=JSON.parse(xhr.responseText)
    identityRedirect(xhr,payload.code)
    if(xhr.status<200||xhr.status>=300)throw new Error(typeof payload.detail==='string'?payload.detail:'Không lưu được video vào Manager.')
    resolve(payload as ReelAsset)
   }catch(reason){reject(reason instanceof Error?reason:new Error('Phản hồi tải video không hợp lệ.'))}
  }
  xhr.onerror=()=>{cleanup();reject(new Error('Mất kết nối khi tải video. Kiểm tra server rồi thử lại.'))}
  xhr.onabort=()=>{cleanup();reject(new Error('Đã hủy tải video lên.'))}
  signal.addEventListener('abort',abort,{once:true});xhr.send(file)
 })
}
