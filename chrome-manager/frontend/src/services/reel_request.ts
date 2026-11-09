// Keep the request key across uncertain retries and page reloads in this tab.
// Only a digest and random ID enter storage; captions and media paths do not.
const storageKey='cm_reel_create_request_v1'
type RequestRecord={fingerprint:string;id:string}
let lastRequest:RequestRecord|null=null

export async function reelRequestId(payload:object):Promise<string>{
 const digest=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(JSON.stringify(payload)))
 const fingerprint=Array.from(new Uint8Array(digest),byte=>byte.toString(16).padStart(2,'0')).join('')
 if(!lastRequest){try{const saved=JSON.parse(sessionStorage.getItem(storageKey)||'null') as RequestRecord|null;if(saved&&/^[0-9a-f]{64}$/.test(saved.fingerprint)&&/^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/.test(saved.id))lastRequest=saved}catch{/* Storage may be unavailable. The in-memory key still protects retries. */}}
 if(lastRequest?.fingerprint===fingerprint)return lastRequest.id
 lastRequest={fingerprint,id:crypto.randomUUID()}
 try{sessionStorage.setItem(storageKey,JSON.stringify(lastRequest))}catch{/* Keep the in-memory key. */}
 return lastRequest.id
}
