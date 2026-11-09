import { ChevronLeft, ChevronRight, ChevronsLeft, ChevronsRight } from 'lucide-react'

interface Props {
  total:number; page:number; pageSize:number; label?:string; selected?:number;
  onPageChange:(page:number)=>void; onPageSizeChange:(size:number)=>void;
}
export function Pagination({total,page,pageSize,label='records',selected=0,onPageChange,onPageSizeChange}:Props){
  const pages=Math.max(1,Math.ceil(total/pageSize))
  const current=Math.max(1,Math.min(page,pages))
  const from=total?(current-1)*pageSize+1:0
  const to=Math.min(current*pageSize,total)
  const numbers=Array.from(new Set([
    1,pages,...Array.from({length:5},(_,index)=>current+index-2),
    ...(current<=3?[2,3,4,5]:[]),...(current>=pages-2?[pages-4,pages-3,pages-2,pages-1]:[])
  ])).filter(n=>n>=1&&n<=pages).sort((a,b)=>a-b)
  return <nav className="table-footer pagination-footer" aria-label={label+' pagination'}>
    <div className="pagination-summary"><span><strong>{from.toLocaleString()}–{to.toLocaleString()}</strong> of <strong>{total.toLocaleString()}</strong> {label}</span>{selected>0&&<span className="selected-count">{selected} selected</span>}</div>
    <div className="pagination-controls">
      <label>Rows per page<select aria-label="Rows per page" value={pageSize} onChange={e=>onPageSizeChange(Number(e.target.value))}>{[10,25,50,100].map(size=><option key={size} value={size}>{size}</option>)}</select></label>
      <div className="pagination-buttons">
        <button type="button" className="page-arrow" aria-label="First page" title="First page" disabled={current===1} onClick={()=>onPageChange(1)}><ChevronsLeft size={15}/></button>
        <button type="button" className="page-arrow" aria-label="Previous page" title="Previous page" disabled={current===1} onClick={()=>onPageChange(current-1)}><ChevronLeft size={15}/></button>
        {numbers.map((number,index)=><span className="page-number-group" key={number}>{index>0&&number-numbers[index-1]>1&&<span className="page-ellipsis">…</span>}<button type="button" className={'page-number '+(number===current?'active':'')} aria-label={'Go to page '+number} aria-current={number===current?'page':undefined} onClick={()=>onPageChange(number)}>{number}</button></span>)}
        <button type="button" className="page-arrow" aria-label="Next page" title="Next page" disabled={current===pages} onClick={()=>onPageChange(current+1)}><ChevronRight size={15}/></button>
        <button type="button" className="page-arrow" aria-label="Last page" title="Last page" disabled={current===pages} onClick={()=>onPageChange(pages)}><ChevronsRight size={15}/></button>
      </div>
    </div>
  </nav>
}
