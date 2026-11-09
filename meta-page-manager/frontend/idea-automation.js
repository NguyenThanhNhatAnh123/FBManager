import {api} from './api.js';
import {el,button,badge,dateTime,number,safeMessage} from './ui.js';

const STATES={disabled:'Đang tắt',coalescing:'Đang chờ thêm idea',blocked:'Chờ đủ điều kiện',running:'Đang xử lý',needs_review:'Cần đối chiếu',idle:'Đang chờ idea',completed:'Đã hoàn tất lượt'};
const REASONS={
 telegram_review_unavailable:'Cần kết nối và bật bot Telegram đúng phiên, đồng thời bật yêu cầu duyệt trước khi tự tạo video.',
 production_pipeline_unavailable:'Bộ điều phối từng video chưa sẵn sàng.',
 uploader_workspace_busy:'Tool upload đang có input, review hoặc batch chưa xong. Giữ dữ liệu hiện có và xử lý trước khi giao việc mới.',uploader_workspace_unknown:'Chưa đọc được trạng thái vùng làm việc tool upload; cần kiểm tra trước khi giao việc mới.',
 preflight_not_checked:'Chưa kiểm tra đủ kết nối cho lượt tự động.',codex_not_authenticated:'Codex chưa xác minh phiên đăng nhập.',codex_image_generation_unavailable:'Codex chưa sẵn sàng tạo ảnh.',uploader_session_unverified:'Chưa xác minh phiên shop Blueox.',producer_workflow_unavailable:'Quy trình tạo ảnh và video chưa sẵn sàng.',production_readiness_unavailable:'Chưa đủ điều kiện sản xuất.',queue_requires_review:'Có lượt trong hàng chờ cần đối chiếu.',auto_batch_requires_review:'Lượt tự động cần đối chiếu trước khi tiếp tục.',auto_batch_already_created:'Hôm nay đã chốt một lượt tự động.',waiting_for_ideas:'Đang chờ idea bạn thêm vào kho.',idea_batch_changed:'Danh sách idea đã thay đổi; đang chờ chốt lại.',automatic_check_failed:'Chưa hoàn tất kiểm tra tự động.',audio_mixes_not_approved:'Cần nghe và chấp nhận đủ các bản mix 8 giây.',
 schedule_unavailable:'Cần lịch 06:00 theo giờ Los Angeles với đủ 14 Page.',page_information_required:'Có Page chưa hoàn tất thay đổi thông tin.',invalid_capacity:'Chưa xác định đủ Page cho kế hoạch.',no_eligible_ideas:'Chưa có idea mới đủ điều kiện.',auto_disabled:'Chế độ tự động đang tắt.',batch_already_claimed:'Kế hoạch hôm nay đã được chốt.',previous_batch_incomplete:'Lượt trước chưa hoàn tất.',
 auto_batch_source_changed:'Nguồn hoặc nhóm Page đã đổi; cần đối chiếu kế hoạch đã khóa.',auto_batch_conflict:'Kế hoạch đã khóa có xung đột; cần đối chiếu trước khi tiếp tục.',consumer_unavailable:'Bộ nhận và kiểm tra video chưa sẵn sàng.'
};
const ITEM_STATES={pending:'Chờ xử lý',queued:'Đã xếp hàng',completed:'Đã hoàn tất',needs_review:'Cần đối chiếu'};
const integer=value=>Number.isSafeInteger(value)&&value>=0?value:null;
const valid=value=>value&&typeof value==='object'&&typeof value.settings?.enabled==='boolean'&&Number.isSafeInteger(value.settings?.revision)&&value.settings.revision>=1;
const entry=(label,value)=>el('div',{},el('dt',{text:label}),el('dd',{text:value}));

export function createIdeaAutomation(){
 const node=el('section',{class:'panel idea-automation','aria-label':'Tự động xử lý idea'});
 let snapshot=null,draft=false,draftRevision=null,dirty=false,saving=false,reading=false,needsRead=false,error='',notice='',generation=0,controller=null;
 function replace(value){if(!valid(value))throw new Error('Invalid automation response');snapshot=value;draft=value.settings.enabled;draftRevision=value.settings.revision;dirty=false;needsRead=false}
 function draw(){
  const loaded=valid(snapshot),busy=saving||reading,active=snapshot?.active_batch;
  const rows=(Array.isArray(active?.items)?active.items:Array.isArray(snapshot?.preview?.allocations)?snapshot.preview.allocations:[])
   .filter(row=>row&&Number.isSafeInteger(row.variant_count)&&row.variant_count>=1&&row.variant_count<=14).slice(0,14);
  const plan=active?.snapshot||snapshot?.preview||{},total=integer(plan.total_videos)??(rows.length?rows.reduce((sum,row)=>sum+row.variant_count,0):null);
  const capacity=integer(plan.capacity)??(Array.isArray(plan.source?.page_ids)?plan.source.page_ids.length:integer(snapshot?.preview?.capacity));
  const candidates=integer(plan.candidate_count),deferred=integer(plan.deferred_count);
  const state=STATES[snapshot?.state]||'Chưa đọc trạng thái';
  const heading=el('div',{class:'panel-heading'},el('div',{},el('h2',{text:'Tự động xử lý idea'}),el('p',{text:'Chỉ dùng idea bạn thêm vào kho; không tự tìm hoặc nhập idea từ mạng.'})),badge(state,snapshot?.state==='needs_review'?'warning':snapshot?.settings?.enabled===true?'success':'neutral'));
  const body=el('div',{class:'panel-body'});
  const checkbox=el('input',{type:'checkbox',checked:draft,disabled:!loaded||busy||needsRead,'aria-label':'Tự động xử lý idea mới',onChange:event=>{draft=event.currentTarget.checked;dirty=draft!==snapshot.settings.enabled;draftRevision=snapshot.settings.revision;notice='';draw()}});
  body.append(el('label',{class:'idea-automation-toggle'},checkbox,' Tự động xử lý idea mới'),el('p',{class:'field-hint',text:'Mặc định tắt. Khi bật, hệ thống kiểm tra kết nối rồi chờ đủ điều kiện để bắt đầu. Tắt sẽ dừng giao việc mới; lượt đang chạy và lịch đã lưu vẫn tiếp tục.'}));
  if(snapshot?.review_policy==='manual_review_v1'&&snapshot?.ai_qa===false)body.append(el('p',{class:'field-hint idea-automation-manual-review',text:'Mỗi video dựng xong được chuyển Telegram để bạn tự xem và duyệt. Sau giờ hẹn, bot nhắc mỗi 5 phút đến khi bạn duyệt hoặc từ chối; duyệt muộn sẽ đăng ngay. Không chạy AI QA.'}));
  if(error)body.append(el('p',{class:'form-error',role:'alert',text:error}));
  if(notice)body.append(el('p',{class:'idea-library-notice',role:'status',text:notice}));
  body.append(el('div',{class:'idea-library-toolbar-actions'},button(saving?'Đang lưu…':'Lưu chế độ tự động',()=>void save(),{style:'primary',small:true,disabled:!loaded||busy||needsRead||!dirty,'data-action':'save-idea-automation'}),button(reading?'Đang đọc…':needsRead?'Đọc lại để đối chiếu':'Đọc trạng thái tự động',()=>void read(),{small:true,disabled:busy,'data-action':'read-idea-automation'})));
  const preview=el('section',{class:'idea-automation-preview','aria-label':active?'Kế hoạch tự động đã khóa':'Kế hoạch tự động dự kiến'},el('h3',{text:active?'Kế hoạch đã khóa':'Kế hoạch dự kiến'}));
  preview.append(el('p',{class:'idea-automation-summary',text:loaded&&total!==null?number(rows.length)+' idea → '+number(total)+' video':'Chưa có kế hoạch đủ dữ liệu'}),el('dl',{class:'idea-library-definitions'},entry('Nguồn idea','Kho idea do bạn thêm'),entry('Page trong lịch',capacity===null?'Chưa xác định':number(capacity)+' Page'),entry('Giới hạn mỗi ngày','Một lượt · tối đa 14 video'),entry('Lịch đăng','06:00 · Los Angeles'),entry('Ngày kế hoạch',typeof plan.day_key==='string'?plan.day_key:typeof active?.day_key==='string'?active.day_key:'Chưa chốt')));
  if(candidates!==null)preview.append(el('p',{class:'idea-library-muted',text:number(candidates)+' idea đủ điều kiện'+(deferred?'; '+number(deferred)+' idea chờ lượt sau.':'.')}));
  if(rows.length){
   const table=el('table'),tbody=el('tbody');
   table.append(el('thead',{},el('tr',{},['Idea trong lượt','Số video','Trạng thái'].map(label=>el('th',{scope:'col',text:label})))));
   rows.forEach((row,index)=>tbody.append(el('tr',{},el('td',{text:'Idea '+number(integer(row.position)??index+1)}),el('td',{text:number(row.variant_count)}),el('td',{text:active?ITEM_STATES[row.status]||'Chờ xác minh':'Dự kiến'}))));
   table.append(tbody);preview.append(el('div',{class:'table-wrap'},table));
  }
  preview.append(el('p',{class:'field-hint',text:active?'Danh sách idea và số video của lượt này đã khóa. Idea thêm sau sẽ chờ lượt tiếp theo.':'Số video được chia đều cho idea đã chọn. Hệ thống chờ '+number(integer(snapshot?.coalesce_seconds)??60)+' giây sau thay đổi idea rồi mới chốt lượt.'}));
  const reasons=[...(Array.isArray(snapshot?.blockers)?snapshot.blockers:[]),...(Array.isArray(snapshot?.preview?.blocker_codes)?snapshot.preview.blocker_codes:[])];
  const messages=[...new Set(reasons.map(code=>typeof code==='string'&&Object.hasOwn(REASONS,code)?REASONS[code]:'Chưa đủ điều kiện tự động; đọc lại trạng thái để kiểm tra.'))];
  if(messages.length)preview.append(el('ul',{class:'idea-library-blockers'},messages.map(text=>el('li',{text}))));
  body.append(preview);if(snapshot?.checked_at)body.append(el('small',{class:'idea-library-muted',text:'Kiểm tra gần nhất: '+dateTime(snapshot.checked_at,'America/Los_Angeles')}));
  node.replaceChildren(heading,body);
 }
 async function read(){
  if(reading||saving)return;const stamp=++generation;reading=true;error='';notice='';controller=new AbortController();draw();
  try{const value=await api.ideaAutomation(controller.signal);if(stamp===generation){replace(value);notice='Đã đọc lại chế độ đang lưu. Kiểm tra kế hoạch trước khi thay đổi.'}}
  catch(reason){if(stamp===generation&&reason.name!=='AbortError')error=safeMessage(reason)}
  finally{if(stamp===generation){reading=false;controller=null;draw()}}
 }
 async function save(){
  if(saving||reading||needsRead||!dirty||!valid(snapshot))return;
  const stamp=++generation,body={enabled:draft,revision:draftRevision};saving=true;error='';notice='';draw();
  try{const value=await api.saveIdeaAutomation(body);if(stamp===generation){replace(value);notice=value.settings.enabled?'Đã bật tự động. Hệ thống sẽ chờ đủ điều kiện trước khi giao việc.':'Đã tắt giao việc tự động mới. Lượt đang chạy và lịch đã lưu vẫn tiếp tục.'}}
  catch(reason){if(stamp===generation){needsRead=true;error=(reason.status===0||reason.status>=500||reason.status===200?'Chưa rõ kết quả lưu. Không gửi lại yêu cầu; đọc lại trạng thái để đối chiếu.':safeMessage(reason)+' Đọc lại trạng thái trước khi lưu lại.')}}
  finally{if(stamp===generation){saving=false;draw()}}
 }
 function adopt(value){
  if(!valid(value)||saving||reading||needsRead)return;
  if(snapshot&&JSON.stringify(snapshot)===JSON.stringify(value))return;
  if(dirty&&snapshot?.settings.revision!==value.settings.revision){snapshot=value;needsRead=true;error='Chế độ đã thay đổi ở nơi khác. Đọc lại trạng thái trước khi lưu lại.';draw();return}
  if(dirty)snapshot=value;else replace(value);draw();
 }
 function dispose(){generation++;controller?.abort();controller=null;if(saving){needsRead=true;error='Chưa rõ kết quả lưu. Đọc lại trạng thái để đối chiếu.'}saving=false;reading=false}
 draw();return {node,adopt,dispose};
}
