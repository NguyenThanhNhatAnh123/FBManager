import { useCallback, useEffect, useRef, useState } from 'react'
import { api, ensureChromeSession } from '../services/api'
import type { System } from '../services/types'
import { invalidateWorkspace, setWorkspaceRevisionFeed } from './useServerPage'

function statusSignature(system:System){
  return JSON.stringify([system.total_profiles,system.running_browsers,system.queued_browsers,
    system.stopped_browsers,system.total_proxies,system.working_proxies,system.failed_proxies,
    system.checking_proxies,system.running_tasks,system.pending_tasks,system.proxy_routing])
}

export function useManager(){
  const [system,setSystem]=useState<System|null>(null)
  const [connected,setConnected]=useState(false)
  const [error,setError]=useState('')
  const statusRequest=useRef<Promise<void>|null>(null)

  const readStatus=useCallback(()=>{
    if(statusRequest.current)return statusRequest.current
    statusRequest.current=api<System>('/system/status')
      .then(value=>{setSystem(value);setError('')})
      .catch(reason=>setError((reason as Error).message))
      .finally(()=>{statusRequest.current=null})
    return statusRequest.current
  },[])

  const refresh=useCallback(()=>{
    invalidateWorkspace()
    return readStatus()
  },[readStatus])

  useEffect(()=>{
    let stopped=false
    let socket:WebSocket|undefined
    let retryTimer:number|undefined
    let statusTimer:number|undefined
    let failures=0
    let hasStatus=false
    let receiving=false
    let revision:string|number|undefined
    let signature:string|undefined

    function retry(){
      if(stopped)return
      const delay=Math.min(30000,3000*2**Math.min(failures++,4))
      retryTimer=window.setTimeout(()=>void connect(true),delay)
    }

    async function connect(renew=false){
      try{
        await ensureChromeSession(renew)
        if(stopped)return
        if(!hasStatus)statusTimer=window.setTimeout(()=>{if(!stopped&&!receiving){hasStatus=true;void readStatus()}},1000)
        socket=new WebSocket((location.protocol==='https:'?'wss://':'ws://')+location.host+'/ws?mode=invalidate')
        socket.onopen=()=>{if(!stopped){setConnected(true);setError('')}}
        socket.onmessage=event=>{
          if(stopped)return
          try{
            const data=JSON.parse(event.data) as {system?:System;workspace_revision?:string|number}
            if(!data.system)return
            window.clearTimeout(statusTimer);hasStatus=true
            const recovered=failures>0
            failures=0;receiving=true
            setSystem(data.system)
            const next=data.workspace_revision
            const versioned=(typeof next==='string'&&next.length>0)||Number.isSafeInteger(next)
            setWorkspaceRevisionFeed(versioned)
            const nextSignature=statusSignature(data.system)
            const changed=versioned?revision!==undefined&&revision!==next:signature!==undefined&&signature!==nextSignature
            if(recovered||changed)invalidateWorkspace('live')
            revision=versioned?next:undefined
            signature=nextSignature
          }catch{ /* Unreadable status leaves the fallback active. */ }
        }
        socket.onerror=()=>socket?.close()
        socket.onclose=()=>{
          receiving=false;setWorkspaceRevisionFeed(false)
          if(!stopped&&!hasStatus){window.clearTimeout(statusTimer);hasStatus=true;void readStatus()}
          if(!stopped){setConnected(false);retry()}
        }
      }catch(reason){if(!stopped){setError((reason as Error).message);retry()}}
    }

    void connect()
    const fallback=window.setInterval(()=>{
      if(document.visibilityState==='visible'&&!receiving)void readStatus()
    },30000)
    const visible=()=>{
      if(document.visibilityState==='visible'&&!receiving)void readStatus()
    }
    document.addEventListener('visibilitychange',visible)
    return ()=>{
      stopped=true;setWorkspaceRevisionFeed(false);socket?.close()
      window.clearInterval(fallback);window.clearTimeout(retryTimer);window.clearTimeout(statusTimer)
      document.removeEventListener('visibilitychange',visible)
    }
  },[readStatus])

  return {system,connected,error,refresh}
}
