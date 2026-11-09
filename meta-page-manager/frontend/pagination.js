import {el,button} from './ui.js';

export function pageWindow(total,page,pageSize){
 const count=Math.max(0,Number.isFinite(total)?Math.floor(total):0),size=Math.max(1,Number.isFinite(pageSize)?Math.floor(pageSize):10);
 const pages=Math.max(1,Math.ceil(count/size)),current=Math.max(1,Math.min(Number.isFinite(page)?Math.floor(page):1,pages)),start=(current-1)*size;
 return {page:current,pages,start,end:Math.min(start+size,count),total:count,pageSize:size};
}

export function createPagination({label,page,pageSize,total,onPageChange,onPageSizeChange,pageSizes=[10,25,50,100]}){
 const view=pageWindow(total,page,pageSize),name='Phân trang '+label;
 const act=(action,callback)=>()=>{callback();queueMicrotask(()=>{const nav=[...document.querySelectorAll('.data-pagination')].find(node=>node.getAttribute('aria-label')===name);const control=[...nav?.querySelectorAll('[aria-label]')||[]].find(node=>node.getAttribute('aria-label')===action);if(control&&!control.disabled)control.focus({preventScroll:true})})};
 const nav=el('nav',{class:'data-pagination','aria-label':name});
 nav.append(el('span',{class:'data-pagination-summary',text:(view.total?view.start+1:0)+'–'+view.end+' / '+view.total+' '+label}));
 const sizes=[...new Set([...pageSizes,view.pageSize])].filter(value=>Number.isInteger(value)&&value>0).sort((a,b)=>a-b),sizeLabel='Số mục mỗi trang · '+label;
 const select=el('select',{'aria-label':sizeLabel,onChange:event=>act(sizeLabel,()=>onPageSizeChange(Number(event.target.value)))()},sizes.map(size=>el('option',{value:size,text:size})));select.value=String(view.pageSize);
 const controls=el('div',{class:'data-pagination-controls'},el('label',{},'Mỗi trang',select)),buttons=el('div',{class:'data-pagination-buttons'});
 const arrow=(text,target,disabled)=>button(text,act(text+' · '+label,()=>onPageChange(target)),{small:true,style:'ghost',disabled,'aria-label':text+' · '+label});
 buttons.append(arrow('Trang đầu',1,view.page===1),arrow('Trang trước',view.page-1,view.page===1));
 const numbers=[...new Set([1,view.pages,view.page-1,view.page,view.page+1])].filter(value=>value>0&&value<=view.pages).sort((a,b)=>a-b);
 numbers.forEach((value,index)=>{if(index&&value-numbers[index-1]>1)buttons.append(el('span',{'aria-hidden':'true',class:'data-pagination-gap',text:'…'}));const aria='Trang '+value+' · '+label;buttons.append(button(String(value),act(aria,()=>onPageChange(value)),{small:true,style:'ghost',class:'button ghost small data-pagination-number','aria-label':aria,'aria-current':value===view.page?'page':null}))});
 buttons.append(arrow('Trang sau',view.page+1,view.page===view.pages),arrow('Trang cuối',view.pages,view.page===view.pages));controls.append(buttons);nav.append(controls);return nav;
}
