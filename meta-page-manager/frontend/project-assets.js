import {api} from './api.js';
import {el,badge,button,safeMessage} from './ui.js';

const KINDS={local:'Cục bộ',native_codex:'Tool native Codex',service:'Dịch vụ / MCP'};
export function createProjectAssets(){
 const node=el('section',{class:'panel idea-project-assets','aria-label':'Skill và tool trong project'});
 let snapshot=null,open=false,reading=false,error='',generation=0,controller=null;
 function draw(){
  const rows=(snapshot?.items||[]).filter(row=>row&&typeof row.id==='string'&&typeof row.name==='string'&&Object.hasOwn(KINDS,row.kind)).slice(0,100);
  const body=el('div',{class:'panel-body'}),content=el('div');
  const details=el('details',{open,onToggle:event=>{
   if(open===event.currentTarget.open)return;
   open=event.currentTarget.open;
   if(open&&!snapshot)void load();else populate();
  }});
  function populate(){
   content.replaceChildren();if(!open)return;
   if(reading){content.append(el('p',{role:'status',text:'Đang đọc danh mục thành phần…'}));return}
   if(error){content.append(el('p',{class:'form-error',role:'alert',text:error}),button('Đọc lại danh mục',()=>void load(),{small:true}));return}
   if(!snapshot){content.append(button('Đọc danh mục thành phần',()=>void load(),{small:true}));return}
   if(typeof snapshot.project_root==='string')content.append(el('p',{class:'idea-project-root',text:'Project: '+snapshot.project_root}));
   content.append(el('p',{class:'field-hint',text:'Tệp và skill cục bộ nằm trong project. Tool native và dịch vụ dùng kết nối của chúng; trạng thái chạy được kiểm tra riêng.'}));
   const table=el('table'),tbody=el('tbody');
   table.append(el('thead',{},el('tr',{},['Thành phần','Loại','Vị trí / cung cấp'].map(text=>el('th',{scope:'col',text})))));
   for(const row of rows){
    const location=row.kind==='local'&&typeof row.path==='string'?row.path:typeof row.provider==='string'?row.provider:'Kiểm tra trong kết nối producer';
    tbody.append(el('tr',{},el('td',{},el('strong',{text:row.name}),row.kind==='local'?badge(row.present===true?'Đã có':row.present===false?'Chưa có':'Chưa kiểm tra',row.present===true?'success':'neutral'):null),el('td',{text:KINDS[row.kind]}),el('td',{},el('code',{text:location}))));
   }
   table.append(tbody);content.append(el('div',{class:'table-wrap'},table));
  }
  details.append(el('summary',{text:snapshot?'Xem '+rows.length+' thành phần trong quy trình':'Xem thành phần trong quy trình'}),content);populate();body.append(details);
  node.replaceChildren(el('div',{class:'panel-heading'},el('div',{},el('h2',{text:'Skill và tool trong project'}),el('p',{text:'Danh mục chỉ đọc của kho media, quy trình và công cụ cần dùng.'}))),body);
 }
 function adopt(value){
  if(!value||!Array.isArray(value.items)||JSON.stringify(value)===JSON.stringify(snapshot))return;
  snapshot=value;error='';draw();
 }
 async function load(){
  if(reading)return;
  const stamp=++generation;reading=true;error='';controller=new AbortController();draw();
  try{const value=await api.projectAssets(controller.signal);if(stamp!==generation)return;if(!Array.isArray(value?.items))throw new Error('Danh mục thành phần chưa hợp lệ.');snapshot=value}
  catch(reason){if(stamp===generation&&reason.name!=='AbortError')error=safeMessage(reason)}
  finally{if(stamp===generation){reading=false;controller=null;draw()}}
 }
 function dispose(){generation++;controller?.abort();controller=null;reading=false;draw()}
 draw();return {node,adopt,dispose};
}
