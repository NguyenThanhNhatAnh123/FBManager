import { api, ensureChromeSession } from './api'
import { identityHeaders, identityRedirect } from './identity'
import type { PageRecipientConfig, PageRecipientMailCheck, PageRecipientStart } from './types'

export const recipientConfig=()=>api<PageRecipientConfig>('/page-recipient/config')
export const startRecipient=(data:PageRecipientStart)=>api('/page-recipient/start','POST',data)
export const stopRecipient=(profileId:number)=>api('/page-recipient/'+profileId+'/stop','POST')
export const resumeRecipient=(profileId:number)=>api('/page-recipient/'+profileId+'/resume','POST')
export const sendRecipientCode=(profileId:number,code:string)=>api('/page-recipient/'+profileId+'/code','POST',{code})

export async function checkRecipientMail(profileId:number):Promise<PageRecipientMailCheck>{
 const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),60000)
 try{
  await ensureChromeSession()
  const response=await fetch('/api/v1/page-recipient/'+profileId+'/check-mail',{method:'POST',credentials:'same-origin',signal:controller.signal,headers:await identityHeaders()})
  const value=await response.json().catch(()=>({}))
  identityRedirect(response,value.code)
  if(!response.ok)throw new Error('OUTLOOK_MAIL_CHECK_FAILED')
  return value as PageRecipientMailCheck
 }catch(reason){
  if(controller.signal.aborted)throw new Error('OUTLOOK_MAIL_CHECK_TIMEOUT')
  throw reason
 }finally{clearTimeout(timer)}
}
