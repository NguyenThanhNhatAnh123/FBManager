import { useCallback, useEffect, useRef, useState } from 'react'
import { ensureChromeSession } from '../services/api'
import { identityRedirect } from '../services/identity'

export interface ServerPage<T>{items:T[];total:number;limit:number;offset:number;summary?:Record<string,unknown>}
type Params=Record<string,string|number|boolean|null|undefined>
type RefreshMode='live'|'manual'|'fallback'
const workspaceRefreshEvent='cm:refresh'
let revisionFeedAvailable=false

export function setWorkspaceRevisionFeed(available:boolean){
 revisionFeedAvailable=available
}

export function invalidateWorkspace(reason:'manual'|'live'='manual'){
 window.dispatchEvent(new CustomEvent(workspaceRefreshEvent,{detail:reason}))
}

export function watchWorkspaceUpdates(refresh:()=>void|Promise<void>,mode:RefreshMode='live'){
 let pending:number|undefined
 const update=()=>{
  if(document.visibilityState!=='visible'||pending!==undefined)return
  pending=window.setTimeout(()=>{pending=undefined;void refresh()},120)
 }
 const invalidated=(event:Event)=>{
  if(mode!=='manual'||(event as CustomEvent).detail!=='live')update()
 }
 const fallback=mode==='manual'?undefined:window.setInterval(()=>{
  if(mode==='fallback'||!revisionFeedAvailable)update()
 },30000)
 window.addEventListener(workspaceRefreshEvent,invalidated)
 document.addEventListener('visibilitychange',update)
 return ()=>{
  window.clearTimeout(pending);window.clearInterval(fallback)
  window.removeEventListener(workspaceRefreshEvent,invalidated)
  document.removeEventListener('visibilitychange',update)
 }
}

export function useServerPage<T>(path:string,params:Params,page:number,pageSize:number,setPage:(page:number)=>void,enabled=true){
 const rawQuery=String(params.q??'')
 const [searchQuery,setSearchQuery]=useState(rawQuery)
 useEffect(()=>{if(rawQuery===searchQuery)return;const timer=window.setTimeout(()=>setSearchQuery(rawQuery),250);return()=>window.clearTimeout(timer)},[rawQuery,searchQuery])
 const queryReady=rawQuery===searchQuery
 const [data,setData]=useState<ServerPage<T>>({items:[],total:0,limit:pageSize,offset:0}),[loading,setLoading]=useState(true),[error,setError]=useState('')
 const [loadedUrl,setLoadedUrl]=useState('')
 const query=new URLSearchParams({limit:String(pageSize),offset:String((page-1)*pageSize)})
 for(const [key,value]of Object.entries(params))if(value!==null&&value!==undefined&&value!=='')query.set(key,String(value))
 const url='/api/v1/'+path+'?'+query.toString(),sequence=useRef(0),controller=useRef<AbortController|null>(null)
 const refresh=useCallback(async()=>{
  if(!enabled||!queryReady)return
  const request=++sequence.current;controller.current?.abort();const abort=new AbortController();controller.current=abort
  try{
   await ensureChromeSession()
   const response=await fetch(url,{credentials:'same-origin',signal:abort.signal,headers:{Accept:'application/json'}})
   const value=await response.json().catch(()=>({}))
   if(!response.ok){
    identityRedirect(response,value.code)
    throw new Error(typeof value.detail==='string'?value.detail:'Không đọc được danh sách.')
   }
   if(!Array.isArray(value.items)||!Number.isInteger(value.total)||value.items.length>pageSize)throw new Error('Server chưa nạp API phân trang mới. Mở lại server để tiếp tục.')
   if(request!==sequence.current)return
   setData(value);setLoadedUrl(url);setError('');setLoading(false)
   const last=Math.max(1,Math.ceil(value.total/value.limit))
   const current=Math.min(last,Math.floor(value.offset/value.limit)+1)
   if(current!==page)setPage(current)
  }catch(reason){if(request===sequence.current&&(reason as Error).name!=='AbortError'){setError((reason as Error).message);setLoading(false)}}
 },[url,enabled,queryReady,pageSize,page,setPage])
 useEffect(()=>{
  if(!enabled||!queryReady)return
  setLoading(true);void refresh()
  const stop=watchWorkspaceUpdates(refresh)
  return()=>{stop();sequence.current++;controller.current?.abort()}
 },[refresh,enabled,queryReady])
 return {...data,items:queryReady&&loadedUrl===url?data.items:[],loading:enabled&&(!queryReady||loading||loadedUrl!==url),error,refresh}
}

export function useServerRecord<T>(path:string|null,fallback:T|null=null,mode:RefreshMode='live'){
 const [record,setRecord]=useState<{path:string;value:T|null;error:string}|null>(null)
 const activeRead=useRef<(()=>Promise<void>)|null>(null)
 const refresh=useCallback(()=>activeRead.current?.()??Promise.resolve(),[])
 useEffect(()=>{
  setRecord(null);if(!path)return
  let active=true,controller:AbortController|null=null
  async function read(){
   controller?.abort()
   const current=new AbortController();controller=current
   try{
    await ensureChromeSession()
    const response=await fetch('/api/v1/'+path,{credentials:'same-origin',signal:current.signal})
    const result=await response.json().catch(()=>({}))
    if(!response.ok){
     identityRedirect(response,result.code)
     throw new Error(typeof result.detail==='string'?result.detail:'Không đọc được chi tiết.')
    }
    if(active&&controller===current&&path)setRecord({path,value:result,error:''})
   }catch(reason){
    if(active&&controller===current&&path&&(reason as Error).name!=='AbortError'){
     setRecord(previous=>({path,value:previous?.path===path?previous.value:null,error:(reason as Error).message}))
    }
   }
  }
  activeRead.current=read
  void read()
  const stop=watchWorkspaceUpdates(read,mode)
  return()=>{active=false;activeRead.current=null;controller?.abort();stop()}
 },[path,mode])
 const current=record?.path===path?record:null
 return {value:current?.value??fallback,error:current?.error||'',loading:!!path&&!current,refresh}
}
