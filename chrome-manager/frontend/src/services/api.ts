import { identityHeaders, identityRedirect } from './identity'
let sessionReady=false
let sessionRequest:Promise<void>|null=null

export function ensureChromeSession(renew=false):Promise<void>{
  if(sessionRequest)return sessionRequest
  if(sessionReady&&!renew)return Promise.resolve()
  sessionReady=false
  sessionRequest=api('/session').then(()=>{sessionReady=true}).finally(()=>{sessionRequest=null})
  return sessionRequest
}

export async function api<T = unknown>(path:string, method='GET', data?:unknown,signal?:AbortSignal):Promise<T> {
  if(path!=='/session')await ensureChromeSession()
  const url=path==='/session'?'/api/session':'/api/v1'+(path.startsWith('/v1/')?path.slice(3):path)
  const response = await fetch(url, { method, credentials:'same-origin',signal,
    headers: {...(method==='GET'?{}:await identityHeaders()),...(data === undefined ? {} : {'Content-Type':'application/json'})},
    body: data === undefined ? undefined : JSON.stringify(data) })
  const payload = await response.json().catch(()=>({detail:'Server returned an unreadable response.'}))
  if (!response.ok) {
    identityRedirect(response,payload.code)
    const detail=payload.detail
    throw new Error(typeof detail === 'string' ? detail : Array.isArray(detail) ? detail.map((e:{msg:string})=>e.msg).join('; ') : 'Request failed')
  }
  return payload as T
}
