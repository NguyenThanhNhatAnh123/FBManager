import {api} from './api.js';
import {el,button,badge,dateTime,safeMessage} from './ui.js';

const LABELS={codex:'Codex · Producer chính',claude:'Claude · Dự phòng'};
const STATES={not_checked:'Chưa kiểm tra',checking:'Đang kiểm tra',cli_ready:'CLI đã đăng nhập',not_authenticated:'Cần đăng nhập',unavailable:'Chưa tìm thấy CLI',check_failed:'Kiểm tra chưa thành công',stale:'Cần kiểm tra lại'};
const REASONS={check_required:'Bấm Kiểm tra để xác minh phiên đăng nhập trên máy chạy Page Manager.',check_in_progress:'Một lượt kiểm tra đang chạy.',check_expired:'Kết quả kiểm tra đã hết hạn; cần kiểm tra lại.',cli_not_installed:'Cài CLI trên máy chạy Page Manager để kết nối producer.',cli_configuration_invalid:'Đường dẫn CLI đã cấu hình chưa hợp lệ.',cli_changed:'CLI đã thay đổi; cần kiểm tra phiên bản và đăng nhập lại.',not_authenticated:'Đăng nhập CLI trên máy chạy Page Manager rồi kiểm tra lại.',auth_status_unsupported:'CLI chưa hỗ trợ cách kiểm tra đăng nhập này.',auth_check_timeout:'Kiểm tra đăng nhập quá thời gian; có thể thử lại.',auth_check_failed:'Chưa đọc được trạng thái đăng nhập.',auth_response_invalid:'CLI chưa trả trạng thái đăng nhập hợp lệ.',executable_unavailable:'Chưa chạy được CLI đã cấu hình.',diagnostic_timeout:'Kiểm tra kết nối quá thời gian; có thể thử lại.',output_limit:'Phản hồi kiểm tra vượt giới hạn cho phép.'};
const IMAGE_REASONS={flag_disabled:'Khả năng tạo ảnh đang tắt trong Codex.',feature_unsupported:'CLI chưa báo khả năng tạo ảnh.',feature_check_failed:'Chưa đọc được khả năng tạo ảnh.',feature_check_timeout:'Kiểm tra khả năng tạo ảnh quá thời gian.'};
const session=value=>value===true?'Đã đăng nhập':value===false?'Cần đăng nhập':'Chưa xác minh';
const entry=(label,value)=>el('div',{},el('dt',{text:label}),el('dd',{text:value}));

export function createProducerConnections(){
 const node=el('section',{class:'panel idea-producer-connections','aria-label':'Kết nối AI producer'});
 let snapshot=null,loaded=false,reading=false,checking='',error='',generation=0,controller=null;
 function draw(){
  const busy=reading||!!checking;
  const heading=el('div',{class:'panel-heading'},el('div',{},el('h2',{text:'Kết nối AI producer'}),el('p',{text:'Codex sản xuất chính. Claude là phương án dự phòng cần kiểm chứng khả năng sản xuất.'})),button(checking==='all'?'Đang kiểm tra…':'Kiểm tra cả hai',()=>void check('all'),{small:true,disabled:busy,'data-action':'check-producers'}));
  const body=el('div',{class:'panel-body'});
  if(error)body.append(el('p',{class:'form-error',role:'alert',text:error}));
  const rows=el('div',{class:'idea-library-workers'});
  for(const provider of ['codex','claude']){
   const value=snapshot?.providers?.[provider]||{},state=value.checking?'checking':value.status||'not_checked';
   const expired=!!value.expires_at&&Number.isFinite(Date.parse(value.expires_at))&&Date.parse(value.expires_at)<=Date.now();
   const authenticated=expired?null:value.authenticated;
   const image=value.image_generation||{};
   const production=value.production_verified===true?'Đã kiểm chứng':'Chờ kiểm chứng tạo ảnh và QA';
   const imageState=image.verified===true?'Đã kiểm chứng':image.configured===true?'Đã bật · chờ chạy thử':image.configured===false?'Chưa bật':'Chưa xác minh';
   rows.append(el('section',{class:'idea-producer-provider','data-provider':provider},
    el('div',{class:'panel-heading'},el('h3',{text:LABELS[provider]}),badge(STATES[expired?'stale':state]||STATES.not_checked,authenticated===true?'success':'warning')),
    el('dl',{class:'idea-library-definitions'},entry('CLI',value.installed===true?'Đã tìm thấy'+(value.version?' · '+value.version:''):'Chưa tìm thấy'),entry('Phiên đăng nhập',session(authenticated)),entry('Tạo ảnh',imageState),entry('Sản xuất và QA',production),entry('Kiểm tra gần nhất',value.checked_at?dateTime(value.checked_at):'Chưa kiểm tra'),entry('Hiệu lực kiểm tra',value.expires_at?dateTime(value.expires_at):'Chưa có')),
    REASONS[value.reason]?el('p',{class:'idea-library-muted',text:REASONS[value.reason]}):null,
    IMAGE_REASONS[image.reason]?el('p',{class:'idea-library-muted',text:IMAGE_REASONS[image.reason]}):null,
    provider==='claude'?el('p',{class:'idea-library-muted',text:'Claude cần công cụ tạo ảnh và QA tương đương trước khi nhận sản xuất dự phòng.'}):null,
    button(checking===provider?'Đang kiểm tra…':'Kiểm tra '+(provider==='codex'?'Codex':'Claude'),()=>void check(provider),{small:true,disabled:busy,'aria-label':'Kiểm tra kết nối '+(provider==='codex'?'Codex':'Claude')})));
  }
  body.append(rows,el('p',{class:'idea-library-muted',text:'Kiểm tra kết nối xác minh CLI và phiên đăng nhập. Khả năng sản xuất được xác nhận riêng bằng tạo ảnh và QA thực tế.'}),el('p',{class:'idea-library-muted',text:'Lượt đang có kết quả chưa rõ cần được đối chiếu trước khi chuyển agent. Nội dung bắt đầu từ Chạy idea; video đủ QA được tự chuyển vào lịch Page Manager.'}));
  if(!loaded&&!reading)body.append(button('Đọc trạng thái producer',()=>void load(),{small:true,disabled:busy}));
  node.replaceChildren(heading,body);
 }
 async function load({force=false}={}){
  if(reading||checking||loaded&&!force)return;
  const stamp=++generation;reading=true;error='';controller=new AbortController();draw();
  try{const value=await api.producerConnections(controller.signal);if(stamp!==generation)return;snapshot=value;loaded=true}
  catch(reason){if(stamp===generation&&reason.name!=='AbortError')error=safeMessage(reason)}
  finally{if(stamp===generation){reading=false;controller=null;draw()}}
 }
 async function check(provider){
  if(reading||checking)return;
  const stamp=++generation;checking=provider;error='';draw();
  try{const value=await api.checkProducerConnections(provider);if(stamp===generation){snapshot=value;loaded=true}}
  catch(reason){if(stamp===generation)error=safeMessage(reason)}
  finally{if(stamp===generation){checking='';draw()}}
 }
 function dispose(){generation++;controller?.abort();controller=null;reading=false;checking='';draw()}
 function adopt(value){if(!value||typeof value!=='object'||!value.providers||reading||checking)return;snapshot=value;loaded=true;draw()}
 draw();return {node,load,dispose,adopt};
}
