import { api } from './api'

export async function confirmedProfileIds(ids:number[],scope:'regular'|'proxy',canRun=false){
  const requested=[...new Set(ids)]
  const batches=[]
  for(let offset=0;offset<requested.length;offset+=100){
    const query=new URLSearchParams({scope,ids:requested.slice(offset,offset+100).join(',')})
    if(canRun)query.set('can_run','true')
    batches.push(api<{ids:number[]}>('/v1/profile-ids?'+query))
  }
  const allowed=new Set((await Promise.all(batches)).flatMap(batch=>batch.ids))
  return requested.filter(id=>allowed.has(id))
}
