import { useEffect, useState } from 'react'
import { useServerPage } from '../hooks/useServerPage'

interface Choice {id:number;name?:string}
type Params=Record<string,string|number|boolean|null|undefined>
interface Props<T extends Choice>{
 path:string;params?:Params;label:string;value:number;selected?:T|null;onChange:(id:number)=>void;
 display:(item:T)=>string;disabled?:boolean;required?:boolean;autoSelect?:boolean;preferredId?:number;
 emptyLabel?:string;optionDisabled?:(item:T)=>boolean
}

/** A native select backed by one searchable SQL page, with its chosen row pinned. */
export function ResourceSelect<T extends Choice>({path,params={},label,value,selected,onChange,display,disabled=false,required=false,autoSelect=false,preferredId,emptyLabel='Chọn…',optionDisabled}:Props<T>){
 const [search,setSearch]=useState(''),[page,setPage]=useState(1)
 const query=search.trim()
 const data=useServerPage<T>(path,{...params,q:query},page,25,setPage)
 const [pinned,setPinned]=useState<T|null>(null)
 const chosen=data.items.find(item=>item.id===value)||(selected?.id===value?selected:null)||(pinned?.id===value?pinned:null)
 useEffect(()=>{const item=data.items.find(item=>item.id===value);if(item)setPinned(item)},[data.items,value])
 useEffect(()=>{
  if(!autoSelect||value||query||data.loading)return
  const available=data.items.filter(item=>!optionDisabled?.(item))
  const item=available.find(item=>item.id===preferredId)||available[0]
  if(item){setPinned(item);onChange(item.id)}
 },[autoSelect,value,query,data.items,data.loading,preferredId,optionDisabled,onChange])
 const pages=Math.max(1,Math.ceil(data.total/25))
 return <div className="resource-select">
  <input aria-label={'Tìm '+label} placeholder="Tìm để thu hẹp danh sách…" value={search} disabled={disabled} onChange={event=>{setSearch(event.target.value);setPage(1)}}/>
  <select aria-label={label} required={required} value={value||''} disabled={disabled} onChange={event=>{const id=Number(event.target.value);setPinned(data.items.find(item=>item.id===id)||null);onChange(id)}}>
   <option value="">{emptyLabel}</option>
   {!!value&&!data.items.some(item=>item.id===value)&&<option value={value} disabled={!!chosen&&optionDisabled?.(chosen)}>{chosen?display(chosen):'#'+value+' · Đang đọc lựa chọn'}</option>}
   {data.items.map(item=><option key={item.id} value={item.id} disabled={optionDisabled?.(item)}>{display(item)}</option>)}
  </select>
  <div className="resource-select-pages" role="group" aria-label={'Phân trang '+label}>
   <small>{data.loading?'Đang đọc lựa chọn…':`${data.total?((page-1)*25+1):0}–${Math.min(page*25,data.total)} / ${data.total}`}</small>
   {pages>1&&<><button className="button small" type="button" aria-label={'Trang trước '+label} disabled={disabled||page===1} onClick={()=>setPage(page-1)}>Trước</button><span>{page} / {pages}</span><button className="button small" type="button" aria-label={'Trang sau '+label} disabled={disabled||page>=pages} onClick={()=>setPage(page+1)}>Sau</button></>}
  </div>
  {data.error&&<small className="warning" role="alert">{data.error}</small>}
 </div>
}
