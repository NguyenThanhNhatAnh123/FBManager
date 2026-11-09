// Request IDs survive lost responses and reloads without storing caption text.
const assert=require('node:assert/strict')
const fs=require('node:fs')
const path=require('node:path')
const vm=require('node:vm')
const {webcrypto}=require('node:crypto')
const ts=require('../frontend/node_modules/typescript')
const source=fs.readFileSync(path.join(__dirname,'../frontend/src/services/reel_request.ts'),'utf8')
const code=ts.transpileModule(source,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.CommonJS}}).outputText
const records=new Map()
const storage={getItem:key=>records.get(key)||null,setItem:(key,value)=>records.set(key,value)}
function load(sessionStorage=storage){const context={exports:{},crypto:webcrypto,TextEncoder,sessionStorage};vm.runInNewContext(code,context);return context.exports.reelRequestId}
async function main(){
 const requestId=load(),payload={caption:'Private caption fixture',asset_id:'a'.repeat(32),scheduled_local:'2026-12-01T12:00'}
 const first=await requestId(payload)
 assert.equal(await requestId(payload),first)
 assert.equal(await load()(payload),first,'Reload must preserve the uncertain request key')
 assert(![...records.values()].join('').includes(payload.caption),'Storage must contain no caption')
 const changed=await requestId({...payload,caption:'Edited caption'})
 assert.notEqual(changed,first)
 assert.equal(await requestId({...payload,caption:'Edited caption'}),changed)
 const memoryOnly=load({getItem(){throw Error('Storage unavailable')},setItem(){throw Error('Storage unavailable')}})
 assert.equal(await memoryOnly(payload),await memoryOnly(payload))
 const simultaneous=await Promise.all([memoryOnly({...payload,asset_id:'b'}),memoryOnly({...payload,asset_id:'b'})])
 assert.equal(simultaneous[0],simultaneous[1])
 console.log('Reel request ID tests passed: retries, reload, edited payload, storage failure, concurrent submission, caption privacy.')
}
main().catch(error=>{console.error(error);process.exitCode=1})
