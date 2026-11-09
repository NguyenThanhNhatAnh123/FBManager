import {api,resourceUrl} from './api.js';
import {el,button,badge,dateTime,safeMessage} from './ui.js';

const hex=value=>typeof value==='string'&&/^[a-f0-9]{64}$/.test(value);
const valid=value=>value&&typeof value==='object'&&(value.render_signature===null||hex(value.render_signature))&&Array.isArray(value.items);
const identity=value=>JSON.stringify([value?.render_signature,(value?.items||[]).map(row=>[row.id,row.pcm_sha256,row.prepared])]);
function previewUrl(row){const path='/api/idea-pipeline/audio-mixes/'+row.id+'/audio';return hex(row.id)&&row.prepared===true&&hex(row.pcm_sha256)&&row.preview_url===path?resourceUrl(path):null}

export function createAudioMixes(){
 const node=el('section',{class:'panel idea-audio-mixes','aria-label':'Nghe và chấp nhận nhạc'});
 let snapshot=null,selected=new Set(),saving=false,reading=false,needsRead=false,error='',notice='',generation=0,controller=null,redraw=false;
 function replace(value){if(!valid(value))throw new Error('Invalid audio mixes response');snapshot=value;selected.clear();needsRead=false}
 function stopAudio(){for(const audio of node.querySelectorAll('audio'))audio.pause()}
 function draw(){
  stopAudio();
  const loaded=valid(snapshot),busy=saving||reading,rows=loaded?snapshot.items.filter(row=>hex(row?.id)).slice(0,100):[];
  const total=Number.isSafeInteger(snapshot?.source_count)?snapshot.source_count:rows.length;
  const approved=rows.filter(row=>row.status==='approved').length;
  const heading=el('div',{class:'panel-heading'},el('div',{},el('h2',{text:'Nghe và chấp nhận nhạc'}),el('p',{text:'Nghe đúng bản mix 8 giây, rồi xác nhận riêng từng bản bạn chấp nhận.'})),badge(approved+'/'+total+' đã chấp nhận',snapshot?.ready===true?'success':'warning'));
  const body=el('div',{class:'panel-body'});
  if(typeof snapshot?.source_directory==='string')body.append(el('p',{class:'idea-audio-source',text:'Nguồn nhạc: '+snapshot.source_directory}));
  body.append(el('p',{class:'field-hint',text:'Xác nhận gắn với âm thanh cố định và cấu hình dựng hiện tại. Khi nguồn hoặc cấu hình đổi, bản mix mới cần được nghe lại.'}));
  if(error)body.append(el('p',{class:'form-error',role:'alert',text:error}));
  if(notice)body.append(el('p',{class:'idea-library-notice',role:'status',text:notice}));
  const list=el('div',{class:'idea-audio-list'});
  rows.forEach((row,index)=>{
   const name=typeof row.source_filename==='string'?row.source_filename:'Bản nhạc '+(index+1),url=previewUrl(row),approved=row.status==='approved';
   const item=el('article',{class:'idea-audio-item'},el('div',{class:'idea-audio-heading'},el('strong',{text:name}),badge(approved?'Đã chấp nhận':row.prepared===true?'Chờ nghe và xác nhận':'Chưa chuẩn bị',approved?'success':'neutral')));
   if(url){
    item.append(el('audio',{controls:true,preload:'none',src:url,'aria-label':'Nghe bản mix '+name}));
    item.append(el('small',{class:'idea-audio-hash',text:'Bản mix PCM: '+row.pcm_sha256}));
   }else item.append(el('p',{class:'field-hint',text:'Bản mix cố định cần được chuẩn bị trước khi nghe.'}));
   if(approved){if(row.approved_at)item.append(el('small',{class:'idea-library-muted',text:'Chấp nhận lúc '+dateTime(row.approved_at,'America/Los_Angeles')}))}
   else if(url)item.append(el('label',{class:'idea-audio-confirm'},el('input',{type:'checkbox',checked:selected.has(row.id),disabled:busy||needsRead||!hex(snapshot.render_signature),'aria-label':'Đã nghe và chấp nhận bản mix '+name,onChange:event=>{if(event.currentTarget.checked)selected.add(row.id);else selected.delete(row.id);notice='';updateSave()}}),' Đã nghe và chấp nhận bản mix này'));
   list.append(item);
  });
  if(rows.length)body.append(list);else body.append(el('p',{class:'idea-library-muted',text:'Chưa có bản mix cố định để nghe. Việc đọc trạng thái không tạo âm thanh.'}));
  body.append(el('div',{class:'idea-library-toolbar-actions'},button(saving?'Đang lưu xác nhận…':'Xác nhận các bản đã nghe',()=>void save(),{style:'primary',small:true,disabled:!loaded||!hex(snapshot.render_signature)||busy||needsRead||!selected.size,'data-action':'save-audio-approval'}),button(reading?'Đang đọc…':needsRead?'Đọc lại để đối chiếu':'Đọc lại bản mix',()=>void read(),{small:true,disabled:busy,'data-action':'read-audio-mixes'})));
  node.replaceChildren(heading,body);
 }
 function updateSave(){const save=node.querySelector('[data-action="save-audio-approval"]');if(save)save.disabled=saving||reading||needsRead||!hex(snapshot?.render_signature)||!selected.size}
 async function read(){
  if(saving||reading)return;const stamp=++generation;reading=true;error='';notice='';controller=new AbortController();draw();
  try{const value=await api.audioMixes(controller.signal);if(stamp===generation){replace(value);notice='Đã đọc lại trạng thái. Chỉ đánh dấu những bản bạn đã nghe và chấp nhận.'}}
  catch(reason){if(stamp===generation&&reason.name!=='AbortError')error=safeMessage(reason)}
  finally{if(stamp===generation){reading=false;controller=null;draw()}}
 }
 async function save(){
  if(saving||reading||needsRead||!selected.size||!valid(snapshot)||!hex(snapshot.render_signature))return;
  const stamp=++generation,body={mix_ids:[...selected],render_signature:snapshot.render_signature};saving=true;error='';notice='';draw();
  try{const value=await api.approveAudioMixes(body);if(stamp===generation){replace(value);notice='Đã lưu chấp nhận cho đúng các bản mix đã chọn.'}}
  catch(reason){if(stamp===generation){needsRead=true;error=reason.status===0||reason.status>=500||reason.status===200?'Chưa rõ kết quả lưu xác nhận. Đọc lại để đối chiếu trước khi thao tác tiếp.':safeMessage(reason)+' Đọc lại bản mix trước khi xác nhận lại.'}}
  finally{if(stamp===generation){saving=false;draw()}}
 }
 function adopt(value){
  if(!valid(value)||saving||reading||needsRead||!redraw&&JSON.stringify(value)===JSON.stringify(snapshot))return;
  redraw=false;
  if(selected.size&&identity(snapshot)!==identity(value)){snapshot=value;selected.clear();needsRead=true;error='Nguồn hoặc bản mix đã đổi. Đọc lại và nghe đúng bản hiện tại trước khi xác nhận.';draw();return}
  if(!selected.size)replace(value);else snapshot=value;
  draw();
 }
 function dispose(){generation++;controller?.abort();controller=null;for(const audio of node.querySelectorAll('audio')){audio.pause();audio.removeAttribute('src');audio.load()}redraw=true;if(saving){needsRead=true;error='Chưa rõ kết quả lưu xác nhận. Đọc lại để đối chiếu.'}saving=false;reading=false}
 draw();return {node,adopt,dispose};
}
