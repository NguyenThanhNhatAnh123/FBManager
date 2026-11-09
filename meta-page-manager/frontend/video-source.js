import {api} from './api.js';
import {canPublishPage,PAGE_INFORMATION_REQUIRED} from './posting-policy.js';
import {el,icon,button,badge,empty,skeleton,safeMessage,dateTime,number} from './ui.js';
import {createPagination} from './pagination.js';

const DEFAULT_CAPTION='Funny tees. Big laughs. Wear your sense of humor.\nShop more laughs: https://blueox179.com\n#FunnyTees #TShirtQuotes #WearYourHumor';
const DEFAULT_ZONE='America/Los_Angeles';
const STATUS={waiting:['Chờ tệp ổn định','neutral'],imported:['Đã nhập','success'],queued:['Đã xếp lịch','success'],skipped:['Đã bỏ qua','neutral'],error:['Cần kiểm tra','danger'],busy:['Đang chờ tài nguyên','warning']};
const string=value=>typeof value==='string'?value:'';
const unique=values=>[...new Set((values||[]).map(String))];
const numeric=value=>/^[0-9]{1,32}$/.test(String(value));

export function createVideoSource({onSchedule,onEditPage}={}){
 const root=el('div',{class:'video-source'}),summaryHost=el('section',{class:'video-source-summary','aria-label':'Tóm tắt nguồn video'}),formHost=el('section',{class:'video-source-form','aria-label':'Cấu hình nguồn video'}),statusHost=el('section',{class:'video-source-status','aria-label':'Trạng thái nguồn video'});
 let mounted=false,initialized=false,status=null,catalog=[],connections=[],draft=null,draftRevision=null,dirty=false,loading=false,saving=false,scanning=false,polling=false,poll=null,error='',statusError='',notice='',catalogError='',sequence=0,loadSequence=0;
 let itemsPage=1,itemsPageSize=10;
 let configOpen=false,metaOpen=false,optionsInitialized=false,optionsLoading=false,optionsSequence=0;
 const expandedItems=new Set();
 let choicesPage=1,choicesSize=25,choicesSearch='',choicesMeta=null,choicesLoading=false,choicesSequence=0,choicesTimer=null,connectionSearch='',connectionOffset=0,connectionMeta=null,connectionSequence=0,connectionTimer=null;
 const reads=new Map();
 const hasItemPages=value=>Number.isSafeInteger(value?.total)&&value.total>=0&&Number.isSafeInteger(value?.offset)&&value.offset>=0&&Number.isSafeInteger(value?.limit)&&value.limit>0;
 const readSource=signal=>api.videoSource({limit:itemsPageSize,offset:(itemsPage-1)*itemsPageSize},signal);
 const readChoices=signal=>api.pages({timezone:DEFAULT_ZONE,limit:choicesSize,offset:(choicesPage-1)*choicesSize,search:choicesSearch},signal);
 async function readConnections(signal){try{return await api.connectionOptions({limit:100,offset:connectionOffset,search:connectionSearch},signal)}catch(reason){if(reason.status!==404)throw reason;return api.connections({limit:100,offset:connectionOffset,search:connectionSearch},signal)}}
 function acceptChoices(value){catalog=value.items||[];choicesMeta=Number.isSafeInteger(value.total)?{total:value.total,offset:value.offset||0}:null}
 function acceptConnections(value){const selected=connections.find(row=>String(row.id)===String(draft?.connection_id));connections=(value.items||[]).slice(0,100);if(selected&&!connections.some(row=>row.id===selected.id))connections.push(selected);connectionMeta=Number.isSafeInteger(value.total)?{total:value.total,offset:value.offset||0}:null}
 function cancelRead(key){const pending=reads.get(key);if(pending){reads.delete(key);pending.controller.abort()}}
 function cancelReads(){for(const key of reads.keys())cancelRead(key)}
 async function read(call,key,signature){
  const previous=reads.get(key);
  if(previous&&previous.signature===signature)return previous.promise;
  cancelRead(key);
  const controller=new AbortController(),pending={controller,signature,promise:call(controller.signal)};
  reads.set(key,pending);
  try{return await pending.promise}finally{if(reads.get(key)===pending)reads.delete(key)}
 }
 const sourceRead=()=>read(readSource,'status',JSON.stringify([itemsPageSize,itemsPage]));
 const choicesRead=()=>read(readChoices,'choices',JSON.stringify([choicesSize,choicesPage,choicesSearch]));
 const connectionsRead=()=>read(readConnections,'connections',JSON.stringify([connectionOffset,connectionSearch]));
 function linked(page,cid=draft?.connection_id){return (page.connections||[]).some(source=>String(source.id)===String(cid)&&source.status!=='disconnected')}
 function pages(){return [...new Map(catalog.filter(page=>numeric(page.id)).map(page=>[String(page.id),page])).values()]}
 function blockedPageIds(){return new Set([...(Array.isArray(status?.posting_blocked_page_ids)?status.posting_blocked_page_ids:[]),...pages().filter(page=>!canPublishPage(page)).map(page=>String(page.id))])}
 function pausingExisting(){return status?.configured===true&&status.settings.enabled===true&&draft?.enabled===false}
 function validStatus(value){return value&&value.settings&&typeof value.settings.enabled==='boolean'&&typeof value.settings.directory==='string'&&Array.isArray(value.settings.page_ids)&&typeof value.configured==='boolean'}
 function resetDraft(){
  if(!status)return;
  const settings=status.settings,blocked=blockedPageIds(),ids=(status.configured?unique(settings.page_ids):choicesMeta&&choicesMeta.total>catalog.length?[]:pages().filter(canPublishPage).map(page=>String(page.id))).filter(id=>!blocked.has(id));
  draftRevision=Number.isInteger(settings.revision)&&settings.revision>=1?settings.revision:null;
  draft={enabled:settings.enabled,page_ids:ids,connection_id:Number(settings.connection_id)||1,daily_time:string(settings.daily_time)||'06:00',timezone:string(settings.timezone)||DEFAULT_ZONE,caption_template:typeof settings.caption_template==='string'?settings.caption_template:DEFAULT_CAPTION};dirty=status.configured&&ids.length!==unique(settings.page_ids).length;
 }
 function change(){dirty=true;error='';notice='';renderActions()}
 function adopt(value){if(!validStatus(value))throw new Error('Invalid source status');status=value}
 async function load(){
  const current=++loadSequence,stamp=++sequence;loading=true;error='';catalogError='';render();
  const values=await Promise.allSettled([sourceRead()]);
  if(!mounted||current!==loadSequence)return;
  if(values[0].status==='fulfilled'&&stamp===sequence){try{adopt(values[0].value);if(!draft)resetDraft()}catch{error='Server trả về cấu hình nguồn video chưa hợp lệ.'}}
  else if(values[0].status==='rejected'&&values[0].reason?.name!=='AbortError')error='Nguồn video chưa sẵn sàng trên server. '+safeMessage(values[0].reason);
  loading=false;initialized=!!status;render();
 }
 async function loadConfigurationChoices(){
  if(optionsLoading||optionsInitialized)return;const stamp=++optionsSequence;optionsLoading=true;catalogError='';renderForm();
  const pageStamp=choicesSequence,connectionStamp=connectionSequence;
  const values=await Promise.allSettled([choicesRead(),connectionsRead()]);
  if(!mounted||stamp!==optionsSequence)return;
  if(pageStamp===choicesSequence){if(values[0].status==='fulfilled')acceptChoices(values[0].value);else if(values[0].reason.name!=='AbortError')catalogError='Chưa đọc được danh sách Page đã lưu.'}
  if(connectionStamp===connectionSequence){if(values[1].status==='fulfilled')acceptConnections(values[1].value);else if(values[1].reason.name!=='AbortError')catalogError='Chưa đọc được các kết nối đã lưu.'}
  optionsInitialized=values.every(value=>value.status==='fulfilled');optionsLoading=false;
  if(!status.configured&&!dirty)resetDraft();
  if(draft&&!connections.some(row=>String(row.id)===String(draft.connection_id)))connections.push({id:draft.connection_id,label:'Nguồn đã lưu #'+draft.connection_id,status:'unchecked'});
  renderForm();renderBrief();
 }
 async function pollStatus(){
  if(!mounted||loading||polling||saving||scanning)return;
  const stamp=++sequence;polling=stamp;
  try{
   let value=await sourceRead();if(!mounted||stamp!==sequence)return;
   if(hasItemPages(value)){
    const nextPage=Math.max(1,Math.min(itemsPage,Math.max(1,Math.ceil(value.total/itemsPageSize))));
    if(nextPage!==itemsPage){itemsPage=nextPage;value=await sourceRead();if(!mounted||stamp!==sequence)return}
   }
   adopt(value);statusError='';renderStatus();renderActions();
  }
  catch(reason){if(mounted&&stamp===sequence&&reason.name!=='AbortError'){statusError=safeMessage(reason);renderStatus();renderActions()}}
  finally{if(polling===stamp)polling=false}
 }
 function render(){
  if(!mounted)return;root.replaceChildren();
  if(loading&&!status){root.append(skeleton(5));return}
  if(!status||!draft){root.append(el('div',{class:'video-source-error',role:'alert',text:error||'Chưa đọc được nguồn video.'}),button('Đọc lại nguồn video',()=>void load(),{iconName:'refresh'}));return}
  const configuration=el('details',{class:'video-source-config',open:configOpen,onToggle:event=>{
   configOpen=event.currentTarget.open;
   if(configOpen){if(!optionsInitialized)void loadConfigurationChoices();else renderForm()}
   else{optionsSequence++;choicesSequence++;connectionSequence++;optionsLoading=false;choicesLoading=false;cancelRead('choices');cancelRead('connections');formHost.replaceChildren()}
  }},el('summary',{},icon('settings',15),el('span',{text:'Cấu hình nguồn video'}),el('small',{text:'Thư mục, Page nhận và caption'})),formHost);
  root.append(summaryHost,configuration,statusHost);renderBrief();if(configOpen){if(!optionsInitialized)void loadConfigurationChoices();else renderForm()}renderStatus();
 }
 function renderBrief(){
  if(!mounted||!status)return;const settings=status.settings,configured=status.configured&&settings.page_ids.length>0;
  summaryHost.replaceChildren(el('div',{},el('div',{class:'video-source-summary-title'},el('h2',{text:'Nguồn video'}),badge(status.running?'Đang quét':settings.enabled?'Đang bật':'Tạm dừng',status.running?'warning':settings.enabled?'success':'neutral'),dirty?badge('Có thay đổi chưa lưu','warning'):null),el('p',{text:configured?unique(settings.page_ids).length+' Page · '+settings.daily_time+' · '+settings.timezone:'Chưa có cấu hình nhận video'})),el('div',{class:'video-source-toolbar'},button(scanning?'Đang quét…':'Quét & xếp lịch ngay',()=>void scan(),{iconName:'refresh',small:true,disabled:saving||scanning||status.running===true||!configured||settings.enabled!==true||dirty}),button('Xem lịch đăng Page',()=>onSchedule?.(),{iconName:'calendar',small:true})));
 }
 function renderForm(){
  if(!mounted||!draft||!configOpen)return;
  if(optionsLoading){formHost.replaceChildren(skeleton(3));return}
  formHost.replaceChildren();
  const form=el('div',{class:'video-source-fields'});formHost.append(form);
  const disabled=saving||scanning;
  form.append(el('label',{},'Thư mục nguồn cố định',el('input',{value:status.settings.directory,readonly:true,'aria-label':'Thư mục nguồn video',disabled:false}),el('small',{class:'field-hint',text:'Nhận MP4, MOV, M4V tối đa 1 GB; tệp được kiểm tra ổn định trước khi nhập.'})),el('label',{class:'video-source-toggle'},el('input',{type:'checkbox',checked:draft.enabled,disabled,'aria-label':'Bật nhập video tự động',onChange:event=>{draft.enabled=event.target.checked;change()}}),el('span',{},el('strong',{text:'Bật nhập video tự động'}),el('small',{text:'Tắt chỉ dừng nhập video mới. Những lịch đã tạo vẫn tiếp tục chạy.'}))));
  const source=el('select',{'aria-label':'Nguồn token cho thư mục video',disabled:disabled||!!catalogError,onChange:event=>{draft.connection_id=Number(event.target.value);change();renderForm()}},el('option',{value:'',text:'Chọn một kết nối cho các Page'}));
  for(const item of connections.filter(item=>item.connected!==false&&item.has_token!==false))source.append(el('option',{value:item.id,text:item.label+' · #'+item.id}));source.value=String(draft.connection_id);
  const zone=el('input',{value:draft.timezone,'aria-label':'Múi giờ nguồn video',disabled,placeholder:DEFAULT_ZONE,onInput:event=>{draft.timezone=event.target.value;change()}});
  const time=el('input',{type:'time',value:draft.daily_time,'aria-label':'Giờ đăng mỗi ngày',disabled,onInput:event=>{draft.daily_time=event.target.value;change()}});
  form.append(el('div',{class:'video-source-grid'},el('label',{},'Nguồn token chung',source),el('label',{},'Giờ đăng mỗi ngày',time),el('label',{},'Múi giờ',zone)));
  if((connectionMeta?.total||0)>100||connectionSearch||connectionOffset){const query=el('input',{type:'search',value:connectionSearch,'aria-label':'Tìm nguồn token cho video',placeholder:'Tìm tên hoặc ID nguồn token',onInput:event=>{connectionSearch=event.target.value;connectionOffset=0;clearTimeout(connectionTimer);connectionTimer=setTimeout(()=>void loadConnectionChoices(),250)}});form.append(query,el('div',{class:'video-source-selection'},button('Nguồn trước',()=>{connectionOffset=Math.max(0,connectionOffset-100);void loadConnectionChoices()},{small:true,disabled:!connectionOffset}),button('Nguồn sau',()=>{connectionOffset+=100;void loadConnectionChoices()},{small:true,disabled:connectionOffset+100>=(connectionMeta?.total||0)})))}
  const selected=new Set(draft.page_ids),list=el('div',{class:'video-source-pages'});
  for(const page of pages())list.append(el('div',{},el('label',{class:'video-source-page'},el('input',{type:'checkbox',checked:canPublishPage(page)&&selected.has(String(page.id)),disabled:disabled||!canPublishPage(page),'aria-label':'Chia video cho Page '+page.name,onChange:event=>{draft.page_ids=event.target.checked?unique([...draft.page_ids,String(page.id)]):draft.page_ids.filter(id=>id!==String(page.id));change()}}),el('span',{},el('strong',{text:page.name}),el('small',{text:page.id+(canPublishPage(page)?'':' · Chưa đổi thông tin — chưa được bật đăng')+(linked(page)?'':' · Chưa liên kết với nguồn đã chọn')}))),!canPublishPage(page)?button('Chỉnh sửa '+page.name,()=>onEditPage?.(page),{small:true,style:'ghost',disabled:disabled||typeof onEditPage!=='function'}):null));
  const missing=draft.page_ids.filter(id=>!pages().some(page=>String(page.id)===id));
  if(missing.length&&!choicesMeta)list.append(el('p',{class:'video-source-error',text:'Có '+missing.length+' Page đã chọn chưa xuất hiện trong dữ liệu hiện tại. Đọc lại danh sách trước khi lưu.'}));
  const choiceSearch=el('input',{type:'search',value:choicesSearch,'aria-label':'Tìm Page nhận video',placeholder:'Tìm tên Page hoặc Meta ID',disabled,onInput:event=>{choicesSearch=event.target.value;choicesPage=1;clearTimeout(choicesTimer);choicesTimer=setTimeout(()=>void loadPageChoices(),250)}}),choiceTotal=choicesMeta?.total??catalog.length;
  const choices=el('fieldset',{class:'video-source-page-set'},el('legend',{text:'Page nhận video · thứ tự luân phiên'}),el('div',{class:'video-source-selection'},button(choicesMeta&&choicesMeta.total>catalog.length?'Chọn Page trong trang này':'Chọn tất cả Page của nguồn',()=>{const ids=pages().filter(page=>linked(page)&&canPublishPage(page)).map(page=>String(page.id)),blocked=blockedPageIds();draft.page_ids=(choicesMeta&&choicesMeta.total>catalog.length?unique([...draft.page_ids,...ids]):ids).filter(id=>!blocked.has(id));change();renderForm()},{small:true,disabled:disabled||!!catalogError}),button('Bỏ chọn tất cả',()=>{draft.page_ids=[];change();renderForm()},{small:true,style:'ghost',disabled})),choiceSearch,el('p',{class:'field-hint',text:draft.page_ids.length+' Page đã chọn trên tất cả các trang · tối đa 100 Page. '+PAGE_INFORMATION_REQUIRED}),choicesLoading?skeleton(3):pages().length?list:el('p',{class:'field-hint',text:choicesSearch?'Không có Page phù hợp với tìm kiếm.':'Chưa có Page đã lưu. Đồng bộ kết nối trong tab Kết nối token trước.'}));
  choices.append(createPagination({label:'Page nhận video',page:choicesPage,pageSize:choicesSize,total:choiceTotal,pageSizes:[25,50,100],onPageChange:value=>{choicesPage=value;choicesMeta?void loadPageChoices():renderForm()},onPageSizeChange:value=>{choicesSize=value;choicesPage=1;void loadPageChoices()}}));form.append(choices);
  form.append(el('label',{},'Caption mặc định',el('textarea',{rows:4,maxlength:2200,value:draft.caption_template,'aria-label':'Caption mặc định cho video',disabled,onInput:event=>{draft.caption_template=event.target.value;change()}}),el('small',{class:'field-hint',text:'Tối đa 2.200 ký tự. Tệp .txt cùng tên sẽ thay caption này; nên có trước hoặc cùng lúc đưa video vào thư mục. Lịch đã tạo giữ caption đã lưu.'})));
  form.append(el('div',{class:'video-source-review',id:'video-source-review'}),el('div',{id:'video-source-actions'}));renderActions();
 }
 function renderActions(){
  if(!mounted||!draft)return;
  renderBrief();
  const summary=root.querySelector('#video-source-review'),actions=root.querySelector('#video-source-actions');if(!summary||!actions)return;
  const source=connections.find(item=>String(item.id)===String(draft.connection_id));
  summary.replaceChildren(el('strong',{text:'Tiêu chí sẽ lưu'}),el('p',{text:(draft.enabled?'Bật tự động':'Tạm dừng nhập mới')+' · '+draft.page_ids.length+' Page · '+(source?.label||'Chưa chọn nguồn')+' · '+draft.daily_time+' '+draft.timezone}),el('p',{text:'Một video chỉ giao cho một Page; mỗi Page một Reel/ngày. Tệp đã nhập không tự nhập lại. Quét sẽ tạo lịch trong hàng đợi, không đăng ngay tại nút quét.'}));
  if(pausingExisting())summary.append(el('p',{text:'Tạm dừng nguồn hiện có sẽ giữ nguyên Page và các cài đặt đã lưu; không áp dụng những thay đổi khác trong bản nháp.'}));
  actions.replaceChildren();
  if(error)actions.append(el('p',{class:'video-source-error',role:'alert',text:error}));
  if(catalogError)actions.append(el('p',{class:'video-source-error',role:'alert',text:catalogError}));
  if(notice)actions.append(el('p',{class:'video-source-notice',role:'status',text:notice}));
  actions.append(el('div',{class:'dialog-actions'},button(saving?'Đang lưu…':'Lưu cấu hình nguồn video',()=>void save(),{style:'primary',iconName:'check',disabled:saving||scanning||!!catalogError&&!pausingExisting()}),button('Nạp lại cấu hình đã lưu',()=>{resetDraft();error='';notice='Đã nạp lại cấu hình đã lưu; bản nháp được thay thế.';renderForm()},{disabled:saving||scanning})),el('p',{class:'field-hint',text:dirty?'Có bản nháp chưa lưu. Lưu cấu hình trước khi quét.':!status.settings.enabled?'Đang tạm dừng nhập mới; có thể xem những lịch đã tạo trong Lịch đăng Page.':'Quét chỉ xếp lịch video mới theo cấu hình đã lưu.'}));
 }
 function validation(){
  if(pausingExisting())return '';
  if(!draft.page_ids.length)return 'Chọn ít nhất một Page nhận video.';
  if(!Number.isInteger(draft.connection_id)||draft.connection_id<1||!connections.some(item=>item.id===draft.connection_id&&item.connected!==false&&item.has_token!==false))return 'Chọn một nguồn token còn hoạt động.';
  if(draft.page_ids.length>100)return 'Chọn tối đa 100 Page nhận video.';
  if(draft.enabled&&draft.page_ids.some(id=>blockedPageIds().has(id)))return PAGE_INFORMATION_REQUIRED;
  if(draft.enabled&&pages().some(page=>draft.page_ids.includes(String(page.id))&&!canPublishPage(page)))return PAGE_INFORMATION_REQUIRED;
  if(draft.page_ids.some(id=>!numeric(id)||(!choicesMeta&&!pages().some(page=>String(page.id)===id&&linked(page)))||pages().some(page=>String(page.id)===id&&!linked(page))))return 'Mỗi Page đã chọn cần liên kết với đúng nguồn token.';
  if(!/^([01]\d|2[0-3]):[0-5]\d$/.test(draft.daily_time))return 'Chọn giờ đăng hợp lệ.';
  try{new Intl.DateTimeFormat('en-US',{timeZone:draft.timezone}).format()}catch{return 'Nhập múi giờ hợp lệ, ví dụ America/Los_Angeles.'}
  if(draft.caption_template.length>2200)return 'Caption mặc định vượt quá 2.200 ký tự.';
  return '';
 }
 async function save(){
  if(saving||scanning||!draft)return;error=validation();if(error){renderActions();return}
  const body=pausingExisting()?Object.fromEntries(['page_ids','connection_id','daily_time','timezone','caption_template'].map(key=>[key,key==='page_ids'?[...status.settings[key]]:status.settings[key]])):{...draft,page_ids:[...draft.page_ids]};
  body.enabled=draft.enabled;
  const revision=pausingExisting()?status.settings.revision:draftRevision;if(Number.isInteger(revision))body.revision=revision;
  saving=true;sequence++;cancelReads();notice='';renderForm();
  try{const value=await api.saveVideoSource(body);adopt(value);itemsPage=1;resetDraft();notice=body.enabled?'Đã lưu và bật nhập tự động. Video mới sẽ được xếp lịch theo tiêu chí đã chọn.':'Đã lưu và tạm dừng nhập mới. Những lịch đã tạo vẫn tiếp tục chạy.'}
  catch(reason){error=safeMessage(reason)+(reason.code==='page_information_required'?' Cấu hình chưa được bật; chỉnh sửa Page rồi chọn lại.':' Không tự gửi lại cấu hình; đọc trạng thái trước khi thao tác tiếp.');if(reason.code==='page_information_required')void loadPageChoices()}
  finally{saving=false;if(mounted){renderForm();renderStatus()}}
 }
 async function scan(){
  if(saving||scanning||dirty||!status?.configured||!status.settings.enabled||status.running)return;
  scanning=true;sequence++;cancelReads();error='';notice='';renderActions();
  try{adopt(await api.scanVideoSource());itemsPage=1;notice='Đã nhận kết quả quét. Xem video và lịch tương ứng bên dưới.'}
  catch(reason){error=safeMessage(reason)+' Chưa xác định đầy đủ kết quả quét; đọc lại trạng thái, không tự quét lại.'}
  finally{scanning=false;if(mounted){renderStatus();renderActions()}}
 }
 function renderStatus(){
  if(!mounted||!status)return;const stats=status.stats||{},next=pages().find(page=>String(page.id)===String(status.next_page_id));
  renderBrief();
  statusHost.replaceChildren(el('div',{class:'video-source-status-heading'},el('div',{},el('h2',{text:'Video trong nguồn'}),el('p',{text:number(stats.files_seen)+' video · '+number(stats.queued)+' đã xếp lịch · '+number(stats.waiting)+' đang chờ'+(stats.errors?' · '+number(stats.errors)+' cần kiểm tra':'')})),button('Đọc lại trạng thái',()=>void pollStatus(),{small:true,iconName:'refresh',disabled:saving||scanning})));
  statusHost.append(el('details',{class:'video-source-meta',open:metaOpen,onToggle:event=>{metaOpen=event.currentTarget.open}},el('summary',{text:'Thông tin nguồn'}),el('p',{text:'Quét gần nhất: '+(status.last_scan_at?dateTime(status.last_scan_at,status.settings.timezone||DEFAULT_ZONE):'Chưa quét')+' · Page kế tiếp: '+(next?.name||status.next_page_id||'Chưa xác định')}),el('p',{text:number(stats.imported)+' đã nhập · '+number(stats.skipped)+' bỏ qua. Mỗi Page một Reel/ngày; video mới được chia luân phiên. Máy và server cần bật để chạy.'})));
  if(status.last_error)statusHost.append(el('p',{class:'video-source-error',role:'alert',text:string(status.last_error)}));
  if(statusError)statusHost.append(el('p',{class:'video-source-error',role:'alert',text:statusError}));
  if(error&&!configOpen)statusHost.append(el('p',{class:'video-source-error',role:'alert',text:error}));
  if(notice&&!configOpen)statusHost.append(el('p',{class:'video-source-notice',role:'status',text:notice}));
  const items=Array.isArray(status.items)?status.items:[];
  const serverPaged=hasItemPages(status),total=serverPaged?status.total:items.length;
  itemsPage=Math.max(1,Math.min(itemsPage,Math.max(1,Math.ceil(total/itemsPageSize))));
  if(!total){statusHost.append(empty(stats.files_seen===0?'Thư mục chưa có video':'Chưa có video được ghi nhận','Video mới sẽ xuất hiện sau khi được quét. Bật và lưu cấu hình để tự nhập; lịch đã tạo tiếp tục được giữ trong Lịch đăng Page.'));return}
  const pager=createPagination({label:'Hàng đợi nguồn video',page:itemsPage,pageSize:itemsPageSize,total,onPageChange:value=>changeItemsPage(value,itemsPageSize),onPageSizeChange:value=>changeItemsPage(1,value)});
  if(!serverPaged)statusHost.append(el('p',{class:'field-hint',text:'Đang hiển thị danh sách video gần nhất do máy chủ cung cấp; chưa xác định tổng số.'}));
  const start=(itemsPage-1)*itemsPageSize,end=Math.min(start+itemsPageSize,total),offset=serverPaged?status.offset:0;
  if(start<offset||end>offset+items.length){statusHost.append(statusError?empty('Chưa đọc được trang video','Bấm Đọc lại trạng thái để tải lại trang đã chọn.'):el('p',{class:'field-hint',role:'status',text:'Đang tải trang video…'}),pager);return}
  const wrap=el('div',{class:'table-wrap video-source-table'}),table=el('table',{}),body=el('tbody');
  table.append(el('thead',{},el('tr',{},['Video','Page nhận','Giờ đăng',''].map(text=>el('th',{scope:'col',text})))));
  for(const item of items.slice(start-offset,end-offset)){
   const [label,tone]=STATUS[item.status]||[string(item.status)||'Chưa xác định','neutral'],key=String(item.id||item.filename+'|'+item.job_id);
   const detail=el('details',{class:'video-source-item-detail',open:expandedItems.has(key),onToggle:event=>{if(event.currentTarget.open)expandedItems.add(key);else expandedItems.delete(key)}},el('summary',{'aria-label':'Chi tiết video '+item.filename,text:'Chi tiết'}),el('dl',{},el('dt',{text:'Kết quả'}),el('dd',{text:item.message||label}),el('dt',{text:'Page ID'}),el('dd',{text:item.page_id||'Chưa phân Page'}),el('dt',{text:'Múi giờ'}),el('dd',{text:item.timezone||status.settings.timezone||DEFAULT_ZONE})),item.job_id?button('Lịch #'+item.job_id,()=>onSchedule?.(),{small:true,style:'ghost'}):null);
   body.append(el('tr',{},el('td',{},el('strong',{text:string(item.filename)||'Video chưa có tên'}),badge(label,tone)),el('td',{},item.page_name||'Chưa phân Page'),el('td',{},item.scheduled_local?item.scheduled_local.replace('T',' ').slice(0,16):'Chưa xếp lịch'),el('td',{},detail)));
  }
  table.append(body);wrap.append(table);statusHost.append(wrap);
  statusHost.append(pager);
 }
 function changeItemsPage(page,size){
  if(saving||scanning)return;itemsPage=page;itemsPageSize=size;
  if(hasItemPages(status)){sequence++;cancelReads();polling=false;statusError='';renderStatus();void pollStatus()}
  else renderStatus();
 }
 async function loadPageChoices(){
  if(saving||scanning)return;const stamp=++choicesSequence;choicesLoading=true;renderForm();
  try{const value=await choicesRead();if(!mounted||stamp!==choicesSequence)return;acceptChoices(value);catalogError='';if(choicesMeta){const last=Math.max(1,Math.ceil(choicesMeta.total/choicesSize));if(choicesPage>last){choicesPage=last;return void loadPageChoices()}}}
  catch(reason){if(mounted&&stamp===choicesSequence&&reason.name!=='AbortError')catalogError=safeMessage(reason)}
  finally{if(mounted&&stamp===choicesSequence){choicesLoading=false;renderForm()}}
 }
 async function loadConnectionChoices(){
  if(saving||scanning)return;const stamp=++connectionSequence;
  try{const value=await connectionsRead();if(!mounted||stamp!==connectionSequence)return;acceptConnections(value);catalogError='';renderForm()}
  catch(reason){if(mounted&&stamp===connectionSequence&&reason.name!=='AbortError'){catalogError=safeMessage(reason);renderForm()}}
 }
 return {
  mount(host){if(mounted&&host.contains(root))return;mounted=true;host.append(root);render();if(!initialized)void load();else{void pollStatus();if(configOpen)void loadPageChoices()}poll=setInterval(()=>{if(document.visibilityState==='visible')void pollStatus()},5000)},
  refresh(){if(mounted&&!loading)return initialized?pollStatus():load()},
  unmount(){if(!mounted)return;mounted=false;clearInterval(poll);clearTimeout(choicesTimer);clearTimeout(connectionTimer);poll=null;sequence++;loadSequence++;choicesSequence++;connectionSequence++;optionsSequence++;cancelReads();polling=false;choicesLoading=false;optionsLoading=false},
  isMounted(host){return mounted&&host.contains(root)},
 };
}
