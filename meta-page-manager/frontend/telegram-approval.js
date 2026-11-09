import {api,resourceUrl} from './api.js';
import {el,button,badge,dateTime,safeMessage} from './ui.js';

const REASONS={credentials_required:'Chưa lưu token bot. Nhập token tại đây; không gửi token vào chat AI.',configuration_incomplete:'Cần chat ID và user ID được phép duyệt.',not_checked:'Lưu cấu hình rồi bấm Kiểm tra bot trước khi bật.',ready:'Token bot đã xác thực; việc duyệt từng video là bước riêng.',invalid_token:'Token bot chưa hợp lệ hoặc đã bị thu hồi.',webhook_active:'Bot đang dùng webhook. Kết nối này không tự thay đổi webhook.',consumer_conflict:'Bot đang có bộ nhận khác. Cần dùng một bộ nhận duy nhất.',network_unavailable:'Chưa kết nối được Telegram; yêu cầu duyệt vẫn được giữ.',check_timeout:'Kiểm tra quá thời gian. Có thể đọc lại trạng thái rồi kiểm tra.',invalid_response:'Telegram chưa trả kết quả hợp lệ.',configuration_changed:'Cấu hình vừa đổi. Đọc lại trạng thái trước khi thao tác.',storage_unavailable:'Chưa đọc được token mã hóa trên tài khoản Windows này.',notification_uncertain:'Thông báo có kết quả chưa rõ; giữ lại để đối chiếu, không tự gửi lại.',approval_unavailable:'Chưa đọc được sổ duyệt. Lịch vẫn bị chặn.'};
const entry=(label,value)=>el('div',{},el('dt',{text:label}),el('dd',{text:value}));
const BATCH_STATES={draft:'Chờ gửi đủ video',sending:'Đang gửi video',sent:'Chờ duyệt trong Telegram',approved:'Đã ghi nhận duyệt nhóm',uncertain:'Cần đối chiếu tin đã gửi',expired:'Quá hạn duyệt',cancelled:'Đã hủy nhóm',invalidated:'Nội dung đã đổi · cần nhóm mới'};
const boundedCount=value=>Number.isSafeInteger(value)&&value>=0&&value<=1e9?value:null;
const awareTime=value=>typeof value==='string'&&/(?:Z|[+-]\d{2}:\d{2})$/.test(value)&&Number.isFinite(Date.parse(value));
const continuousReview=value=>value?.approval_policy==='remind_until_decision_v1';
const reminderHint='Sau giờ đăng, nhắc duyệt mỗi 5 phút đến khi bạn duyệt hoặc từ chối.';

function reminderSummary(value,enabled){
 if(!value||value.interval_seconds!==300)return null;
 const show=name=>{const count=boundedCount(value.counts?.[name]);return count===null?'Chưa xác định':String(count)};
 const targets=boundedCount(value.pending_targets);
 return el('section',{'aria-label':'Nhắc duyệt tự động'},el('h3',{text:'Nhắc duyệt tự động · 5 phút'}),
  el('p',{class:'field-hint',text:enabled?reminderHint+' Duyệt muộn sẽ đăng ngay.':'Bật nhận duyệt Telegram để kích hoạt lời nhắc mỗi 5 phút.'}),
  el('dl',{class:'idea-library-definitions'},entry('Nhóm / video chờ nhắc',targets===null?'Chưa xác định':String(targets)),
   entry('Lần nhắc tiếp theo',awareTime(value.next_reminder_at)?dateTime(value.next_reminder_at,'America/Los_Angeles')+' (Los Angeles)':'Chưa có lượt chờ nhắc'),
   entry('Đã nhắc',show('sent')),entry('Lời nhắc đang gửi',show('sending')),entry('Kết quả nhắc chưa rõ',show('uncertain'))));
}

function batchSummary(value){
 if(!value||typeof value!=='object'||!Array.isArray(value.items))return null;
 const total=boundedCount(value.total),rows=value.items.slice(0,10).filter(row=>row&&typeof row==='object'&&Number.isInteger(row.total)&&row.total>=2&&row.total<=20);
 const node=el('section',{class:'telegram-batches','aria-label':'Nhóm duyệt Telegram'},el('h3',{text:'Nhóm duyệt Telegram'+(total===null?'':' · '+total)}));
 for(const row of rows){
  const id=typeof row.batch_id==='string'&&/^(?:[a-f0-9]{64}|[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12})$/.test(row.batch_id)?row.batch_id.slice(0,8):null;
  const state=typeof row.state==='string'&&Object.hasOwn(BATCH_STATES,row.state)?BATCH_STATES[row.state]:'Trạng thái cần đối chiếu';
  node.append(el('div',{class:'telegram-batch-summary'},el('strong',{text:(id?'Nhóm '+id:'Nhóm duyệt')+' · '+row.total+' video'}),badge(state,row.state==='approved'?'neutral':'warning'),el('p',{class:'field-hint',text:continuousReview(row)?reminderHint:'Hạn duyệt: '+(awareTime(row.approve_by)?dateTime(row.approve_by,'UTC')+' (UTC)':'Chưa xác định')})));
 }
 if(!rows.length)node.append(el('p',{class:'field-hint',text:'Chưa có nhóm duyệt được xác nhận để hiển thị.'}));
 node.append(el('p',{class:'field-hint',text:'Nút duyệt cả nhóm chỉ ở Telegram, sau khi đã gửi và xác nhận đủ từ 2 đến 20 video. Quyết định chỉ áp dụng cho đúng nhóm đã chốt; video thêm sau cần được duyệt riêng.'}));
 return node;
}

function notificationSummary(value){
 if(!value||typeof value!=='object')return null;
 const show=field=>{const count=boundedCount(value.counts?.[field]);return count===null?'Chưa xác định':String(count)};
 return el('section',{'aria-label':'Thông báo sau duyệt và đăng'},el('h3',{text:'Thông báo sau duyệt và đăng'}),el('dl',{class:'idea-library-definitions'},entry('Đã gửi',show('sent')),entry('Chờ gửi',show('pending')),entry('Đang gửi',show('sending')),entry('Cần đối chiếu',show('uncertain')),entry('Không còn hiệu lực',show('invalidated'))));
}
function cohortSummary(value){
 if(!value||typeof value!=='object'||!Number.isInteger(value.total)||value.total<2||value.total>20)return null;
 const labels={active:'Đang nhận duyệt',completed:'Đã ghi nhận đủ quyết định',uncertain:'Cần đối chiếu tin đã gửi',expired:'Đã hết hạn',configuration_changed:'Nội dung hoặc cấu hình đã đổi'};
 const count=name=>{const valueCount=boundedCount(value.counts?.[name]);return valueCount===null?'Chưa xác định':String(valueCount)};
 return el('section',{'aria-label':'Phiên duyệt Telegram riêng'},el('h3',{text:'Phiên duyệt riêng · '+value.total+' video'}),
  badge(Object.hasOwn(labels,value.state)?labels[value.state]:'Trạng thái cần đối chiếu',value.state==='completed'?'neutral':'warning'),
  el('p',{class:'field-hint',text:'Chỉ gửi và nhận duyệt đúng danh sách đã khóa trong phiên này. Gửi video mới toàn cục vẫn tắt; lịch cũ hoặc video thêm sau không thuộc phiên.'}),
  el('dl',{class:'idea-library-definitions'},entry('Đã gửi video',count('delivered')),entry('Đang gửi video',count('sending')),entry('Cần đối chiếu',count('uncertain')),entry('Chờ duyệt',count('pending')),entry('Đã duyệt',count('approved')),entry('Đã từ chối',count('rejected'))),
  el('p',{class:'field-hint',text:'Hạn phiên: '+(awareTime(value.expires_at)?dateTime(value.expires_at,'UTC')+' (UTC)':'Chưa xác định')}));
}
Object.assign(REASONS,{trial_in_progress:'Đang thử một video khác. Hoàn tất hoặc chờ hết phiên thử.',trial_requires_disabled:'Tắt nhận duyệt toàn bộ trước khi thử riêng một video.',trial_unavailable:'Lượt thử hoặc yêu cầu duyệt đã hết hiệu lực.'});

export function createTelegramApproval(){
 const node=el('section',{class:'panel telegram-approval','aria-label':'Duyệt đăng qua Telegram'});
 let snapshot=null,busy='',error='',uncertain=false,dirty=false,generation=0,controller=null;
 function adopt(value){if(!value||typeof value!=='object'||!value.check||busy||dirty)return;snapshot=value;draw()}
 function draw(){
  const value=snapshot||{},check=value.check||{},ready=check.ok===true&&Number.isFinite(Date.parse(check.expires_at))&&Date.parse(check.expires_at)>Date.now();
  const trial=value.trial;
  const counts=value.notifications?.counts,noticeFields=['pending','sending','sent','uncertain','invalidated'];
  const noticesAvailable=noticeFields.some(field=>boundedCount(counts?.[field])!==null);
  const noticeHistory=['pending','sending','sent','uncertain'].some(field=>(boundedCount(counts?.[field])||0)>0);
  const approvedBatch=Array.isArray(value.batches?.items)&&value.batches.items.slice(0,10).some(row=>row?.state==='approved'&&Number.isInteger(row.total)&&row.total>=2&&row.total<=20);
  const following=!value.enabled&&!trial?.active&&value.configured===true&&value.token_saved===true&&check.ok===true&&noticesAvailable&&(noticeHistory||trial?.state==='decided'||approvedBatch);
  const heading=el('div',{class:'panel-heading'},el('h2',{text:'Duyệt đăng qua Telegram'}),badge(value.enabled?'Đã bật nhận duyệt':trial?.active?'Đang thử 1 video':following?'Theo dõi video đã duyệt':'Chưa bật bot',value.enabled||trial?.active||following?'neutral':'warning'));
  const body=el('div',{class:'panel-body'});
  body.append(el('p',{text:value.gate?.required===true?'Đăng Page đang bắt buộc duyệt. Chưa có bot hoặc chưa duyệt thì lịch được giữ lại.':'Chốt bắt buộc duyệt chưa được bật. Kết nối bot không tự thay đổi chính sách đăng.'}));
  body.append(el('ol',{class:'telegram-review-flow','aria-label':'Từ duyệt đến đăng'},el('li',{text:'Bot xác nhận đã ghi nhận quyết định duyệt.'}),el('li',{text:'Video chờ trong lịch nội bộ Via. Máy và server cần hoạt động; hệ thống gửi lên Facebook theo đúng chế độ và giờ đã duyệt.'}),el('li',{text:'Sau khi đăng và xác minh thành công, bot gửi xác nhận kèm link Reel. Kết quả chưa rõ được giữ để đối chiếu.'})));
  if(value.enabled)body.append(el('p',{class:'field-hint',text:'Mỗi video hoàn thành được gửi duyệt riêng, không chờ đủ cả đợt. Nút duyệt nhóm chỉ gom các video cùng đợt đã gửi xác nhận; video thêm sau cần duyệt riêng.'}));
  if(following)body.append(el('p',{class:'field-hint',role:'status',text:'Tiếp tục theo dõi và thông báo kết quả cho video đã duyệt. Tự gửi video mới đang tắt; video thêm sau vẫn cần được gửi và duyệt riêng.'}));
  if(error)body.append(el('p',{class:'form-error',role:'alert',text:error}));
  body.append(el('dl',{class:'idea-library-definitions'},entry('Token đã lưu',value.token_saved?'Có · được mã hóa trên máy':'Chưa có'),entry('Bot',value.bot?.username?'@'+value.bot.username:'Chưa xác thực'),entry('Lần kiểm tra',check.checked_at?dateTime(check.checked_at):'Chưa kiểm tra'),entry('Video đang gửi',String(boundedCount(value.sending_notifications)||0)),entry('Thông báo chưa rõ',String(boundedCount(value.uncertain_notifications)||0))),el('p',{class:'field-hint',text:REASONS[check.reason]||'Chưa xác minh kết nối bot.'}));
  const reminders=reminderSummary(value.reminders,value.enabled===true);if(reminders)body.append(reminders);
  const token=el('input',{type:'password',autocomplete:'new-password','aria-label':'Token bot Telegram',placeholder:value.token_saved?'Để trống để giữ token đã lưu':'Token từ BotFather'});
  const chat=el('input',{type:'text',inputmode:'numeric','aria-label':'Chat ID Telegram',value:value.chat_id||'',placeholder:'ID số, có thể bắt đầu bằng dấu -'});
  const users=el('input',{type:'text','aria-label':'User ID được phép duyệt',value:(value.allowed_user_ids||[]).join(', '),placeholder:'Các user ID số, ngăn cách bằng dấu phẩy'});
  const enabled=el('input',{type:'checkbox',checked:value.enabled===true,disabled:!!busy||uncertain||!value.enabled&&(!ready||!value.configured),'aria-label':'Bật nhận duyệt Telegram'});
  for(const input of [token,chat,users])input.addEventListener('input',()=>{dirty=true;enabled.checked=false;enabled.disabled=true});
  enabled.addEventListener('change',()=>{dirty=true});
  const form=el('form',{class:'telegram-settings',onSubmit:event=>{event.preventDefault();if(busy||uncertain)return;const secret=token.value;token.value='';const payload={revision:value.revision,enabled:enabled.checked,chat_id:chat.value.trim()||null,allowed_user_ids:users.value.split(',').map(v=>v.trim()).filter(Boolean)};if(secret)payload.bot_token=secret;void action('save',()=>api.saveTelegramApproval(payload))}},
   el('label',{},'Token bot',token),el('label',{},'Chat ID nhận video',chat),el('label',{},'User ID được phép duyệt',users),el('label',{class:'telegram-toggle'},enabled,' Bật nhận duyệt Telegram'),
   el('p',{class:'field-hint',text:'Tạo bot bằng BotFather, mở chat với bot và bấm Start. Chỉ lưu token trong ô bảo mật này. Lưu trước, kiểm tra bot, rồi chủ động bật nhận duyệt.'}),
   el('div',{class:'idea-library-toolbar-actions'},el('button',{type:'submit',class:'button primary',disabled:!!busy||uncertain||!snapshot,text:busy==='save'?'Đang lưu…':'Lưu cấu hình'}),button('Kiểm tra bot',()=>void action('check',()=>api.checkTelegramApproval(value.revision)),{small:true,disabled:!!busy||uncertain||!value.token_saved||dirty}),button('Đọc lại trạng thái',()=>void load(),{small:true,disabled:!!busy}),value.token_saved?button('Ngắt kết nối bot',()=>void action('disconnect',()=>api.disconnectTelegramApproval(value.revision)),{small:true,disabled:!!busy||uncertain}):null));
  body.append(form);
  const cohort=cohortSummary(value.cohort);if(cohort)body.append(cohort);
  const batches=batchSummary(value.batches),notifications=notificationSummary(value.notifications);
  if(batches)body.append(batches);
  if(notifications)body.append(notifications);
  const pending=value.pending?.items||[];
  if(trial){const zone=pending.find(ticket=>ticket.request_id===trial.request_id)?.binding?.timezone||'America/Los_Angeles';body.append(el('p',{role:'status',class:'field-hint',text:trial.active?(trial.delivery_state==='sent'?'Đã gửi video thử và nút duyệt.':'Đang gửi một video thử.')+' Nhận quyết định cho video này đến '+dateTime(trial.expires_at,zone)+' ('+zone+').':trial.state==='decided'?'Đã ghi nhận quyết định cho video thử.':trial.state==='uncertain'?'Lượt gửi thử có kết quả chưa rõ; cần đối chiếu tin Telegram.':'Phiên thử đã dừng hoặc hết hạn.'}))}
  body.append(el('h3',{text:'Video chờ duyệt · '+(value.pending?.total||0)}));
  for(const ticket of pending.slice(0,20)){
   const binding=ticket.binding||{},id=ticket.request_id,zone=binding.timezone||'America/Los_Angeles';
   const card=el('details',{class:'telegram-pending'},el('summary',{},el('strong',{text:'Page '+String(binding.page_id||'')+' · '+(binding.queue==='chrome'?'Chrome Manager':'Page Manager')})),el('p',{text:binding.publish_mode==='on_approval'?'Đăng ngay sau khi bạn duyệt':'Lịch: '+dateTime(binding.scheduled_at,zone)+' ('+zone+')'}),el('p',{text:continuousReview(binding)||continuousReview(ticket)?reminderHint:'Hạn duyệt: '+dateTime(ticket.approve_by,zone)+' ('+zone+')'}),el('p',{class:'telegram-caption',text:binding.caption||'(Caption trống)'}));
   if(typeof id==='string'&&/^[a-f0-9-]{36}$/.test(id))card.append(el('video',{controls:true,preload:'none',src:resourceUrl('/api/telegram-approval/requests/'+id+'/video'),'aria-label':'Video chờ duyệt Page '+String(binding.page_id||'')}),button('Gửi thử video này qua Telegram',()=>void action('trial',()=>api.trialTelegramApproval(value.revision,id)),{small:true,'aria-label':'Gửi thử Telegram cho Page '+String(binding.page_id||''),disabled:!!busy||dirty||uncertain||!ready||!value.configured||value.enabled||trial?.active||value.cohort?.active||trial?.request_id===id&&trial?.state!=='configuration_changed'}));
   body.append(card);
  }
  if(!pending.length)body.append(el('p',{class:'field-hint',text:'Chưa có yêu cầu chờ duyệt. Nút duyệt sẽ xuất hiện trong tin Telegram gắn đúng video, Page, caption và lịch.'}));
  body.append(el('p',{class:'field-hint',text:'Duyệt không bỏ qua QA, thông tin Page hoặc giờ đăng. Thay video, Page, caption hay lịch phải duyệt lại; quá hạn thì chọn lịch tương lai.'}));
  node.replaceChildren(heading,body);
 }
 async function action(kind,call){
  if(busy||uncertain)return;const stamp=++generation;busy=kind;error='';dirty=false;draw();
  try{const value=await call();if(stamp===generation){snapshot=value;uncertain=false}}
  catch(reason){if(stamp===generation){error=safeMessage(reason);uncertain=reason.status===0;if(uncertain)error='Chưa rõ kết quả. Đọc lại trạng thái trước khi thao tác tiếp.'}}
  finally{if(stamp===generation){busy='';draw()}}
 }
 async function load(){
  if(busy)return;const stamp=++generation;busy='read';controller=new AbortController();error='';dirty=false;draw();
  try{const value=await api.telegramApproval(controller.signal);if(stamp===generation){snapshot=value;uncertain=false}}
  catch(reason){if(stamp===generation&&reason.name!=='AbortError')error=safeMessage(reason)}
  finally{if(stamp===generation){busy='';controller=null;draw()}}
 }
 function dispose(){generation++;controller?.abort();controller=null;busy='';dirty=false;draw()}
 draw();return {node,adopt,load,dispose};
}
