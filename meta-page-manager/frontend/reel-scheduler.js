import {api,APP_BASE,resourceUrl,readPageWindow} from './api.js';
import {el,icon,button,badge,empty,skeleton,dateTime,facebookUrl,safeMessage,openDialog} from './ui.js';
import {canPublishPage,PAGE_INFORMATION_REQUIRED} from './posting-policy.js';

const STATES={scheduled:['Đã lên lịch','neutral'],waiting_page:['Đang chờ Page / nguồn','warning'],starting:['Đang chuẩn bị','neutral'],uploading:['Đang tải lên Meta','neutral'],processing:['Meta đang xử lý','neutral'],publishing:['Đang gửi đăng','warning'],verifying:['Đang xác minh','neutral'],completed:['Đã đăng','success'],needs_user:['Cần kiểm tra','warning'],needs_review:['Cần xác minh kết quả','warning'],cancelled:['Đã hủy','neutral']};
const string=value=>typeof value==='string'?value:'';
const fileSize=bytes=>Number.isFinite(bytes)?bytes>=1073741824?(bytes/1073741824).toFixed(2)+' GiB':bytes>=1048576?(bytes/1048576).toFixed(1)+' MB':Math.ceil(bytes/1024)+' KB':'Chưa rõ dung lượng';
function assetUrl(value,expectedId){try{const url=new URL(value,location.origin);if(url.origin!==location.origin||url.username||url.password||url.search||url.hash)return null;let path=url.pathname;if(APP_BASE&&path.startsWith(APP_BASE+'/'))path=path.slice(APP_BASE.length);if(!/^\/api\/reels\/assets\/[a-zA-Z0-9_-]+\/video$/.test(path))return null;if(expectedId!==undefined&&(!/^[a-zA-Z0-9_-]+$/.test(String(expectedId))||path!=='/api/reels/assets/'+expectedId+'/video'))return null;return resourceUrl(path)}catch{return null}}
function reelUrl(value){const safe=facebookUrl(value);if(!safe)return null;const url=new URL(safe);return /^\/reel\/\d+\/?$/.test(url.pathname)?'https://www.facebook.com'+url.pathname:null}
function calendarParts(value,zone){
 try{if(typeof value!=='string'||!/(?:Z|[+-]\d{2}:\d{2})$/.test(value)||typeof zone!=='string'||!zone)return null;const date=new Date(value);if(!Number.isFinite(date.getTime()))return null;return Object.fromEntries(new Intl.DateTimeFormat('en-GB',{timeZone:zone,year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).formatToParts(date).map(part=>[part.type,part.value]))}catch{return null}
}
function localDateTime(value){const match=/^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::\d{2})?$/.exec(string(value));return match?`${match[3]}/${match[2]}/${match[1]} ${match[4]}:${match[5]}`:'Chưa chọn ngày giờ'}
function calendarDate(value,zone){const p=calendarParts(value,zone);return p?`${p.year.padStart(4,'0')}-${p.month}-${p.day}`:''}
function calendarLabel(value){const parts=value.split('-');return parts.length===3?`${parts[2]}/${parts[1]}/${parts[0]}`:parts.length===2?`${parts[1]}/${parts[0]}`:value||'Chưa xác định ngày'}
function postingTime(job){return job.publish_mode==='on_approval'?'Đăng ngay sau khi bạn duyệt':dateTime(job.scheduled_at,job.timezone)}
function continuousReview(job){return job.approval_policy==='remind_until_decision_v1'||job.publication_approval?.approval_policy==='remind_until_decision_v1'}
function reviewTime(job){
 if(continuousReview(job)){
  if(job.status==='completed')return 'Đã đăng · lời nhắc đã dừng';
  if(job.status==='cancelled')return 'Đã hủy · lời nhắc đã dừng';
  if(job.publication_approval?.state==='rejected')return 'Đã từ chối · lời nhắc đã dừng';
  if(job.publication_approval?.state==='approved')return 'Đã duyệt · lời nhắc đã dừng';
  return 'Chờ bạn quyết định · nhắc mỗi 5 phút sau giờ đăng';
 }
 return job.publish_mode==='on_approval'?'Hạn duyệt: '+dateTime(job.approve_by,job.timezone):job.timezone||'Chưa xác định múi giờ';
}

export function createTokenReelScheduler({onEditPage}={}){
 const root=el('div',{class:'token-reels'}),formHost=el('section',{class:'token-reels-form-panel'}),jobsHost=el('section',{class:'token-reels-jobs','aria-label':'Hàng đợi Reel bằng token'});
 let mounted=false,mode='create',initialized=false,config=null,configError='',catalog=[],assets=[],jobs=[],catalogError='',assetsError='',jobsError='',loading=false,poll=null,polling=false,activePoll=0,loadGeneration=0,jobsGeneration=0,assetsGeneration=0;
 let draft={pageId:'',connectionId:'',assetId:'',caption:'',scheduledLocal:'',timezone:'',fold:'0',file:null},review=null,creating=false,uploading=false,uploadError='',formError='',saved='',uploadController=null,localPreview=null,search='',filter='all';
 const reads=new Map(),jobBusy=new Set(),jobCards=new Map();
 const pageSize=12;let listPage=1,jobsRows=null,jobsErrorHost=null,jobsPager=null,jobsRefresh=null;
 let calendarMode='today',calendarValue='',calendarSort='recent',calendarControls=null;
 const optionSize=25,optionTimers={catalog:null,assets:null};let catalogSearch='',catalogOffset=0,catalogTotal=null,assetSearch='',assetOffset=0,assetTotal=null,catalogLoaded=false,assetsLoaded=false,catalogGeneration=0,searchTimer=null,jobsMeta=null,jobsReloadRequested=false,jobsQueryKey='',selectedPageRecord=null,selectedAssetRecord=null,detailRecord=null;
 const detailDialog=el('dialog',{id:'token-reels-detail-dialog',class:'token-reels-detail-dialog','aria-labelledby':'token-reels-detail-title'});document.body.append(detailDialog);
 let detailId=null,detailSignature=null;
 detailDialog.addEventListener('close',()=>{detailId=null;detailRecord=null;detailSignature=null;detailDialog.replaceChildren()});
 function pauseVideos(target=root){for(const video of target.querySelectorAll('video'))try{video.pause()}catch{}}
 function cancelReads({keepConfig=false}={}){
  for(const [key,pending] of reads){
   if(keepConfig&&key==='config')continue;
   reads.delete(key);pending.controller.abort();
  }
 }
 async function read(call,key=Symbol()){
  const previous=reads.get(key);
  if(key==='config'&&previous)return previous.promise;
  previous?.controller.abort();
  const controller=new AbortController(),pending={controller,promise:call(controller.signal)};
  reads.set(key,pending);
  try{return await pending.promise}finally{if(reads.get(key)===pending)reads.delete(key)}
 }
 function selectedPage(){return catalog.find(value=>String(value.id)===draft.pageId)||(String(selectedPageRecord?.id)===draft.pageId?selectedPageRecord:undefined)}
 function linkedSources(page=selectedPage()){return (page?.connections||[]).filter(value=>value.status!=='disconnected')}
 function selectedAsset(){return assets.find(value=>String(value.id)===draft.assetId)||(String(selectedAssetRecord?.id)===draft.assetId?selectedAssetRecord:undefined)}
 function clearPreview(){if(localPreview)URL.revokeObjectURL(localPreview);localPreview=null}
 function clearReview(){if(!review?.attempted)review=null;formError='';saved=''}
 function available(){return !!config&&!configError}
 function mergeJob(job){if(!job||job.id===undefined)return;const previous=jobs.find(value=>String(value.id)===String(job.id))||detailRecord;job={...job,...(!job.asset&&previous?.asset?{asset:previous.asset}:{})};if(String(detailId)===String(job.id))detailRecord=job;jobs=[job,...jobs.filter(value=>String(value.id)!==String(job.id))];if(jobsMeta)jobs=jobs.slice(0,pageSize)}
 const isPage=value=>Number.isSafeInteger(value?.total)&&value.total>=0&&Number.isSafeInteger(value?.offset)&&value.offset>=0;
 function catalogWindow(signal){return readPageWindow(api.pages,{timezone:'America/Los_Angeles',limit:optionSize,offset:catalogOffset,search:catalogSearch,posting_eligible_only:true},signal)}
 function assetWindow(signal){return readPageWindow(api.tokenReelAssets,{limit:optionSize,offset:assetOffset,search:assetSearch},signal)}
 function calendarFilter(){
  const pattern=['today','day'].includes(calendarMode)?/^\d{4}-\d{2}-\d{2}$/:calendarMode==='month'?/^\d{4}-\d{2}$/:calendarMode==='year'?/^\d{4}$/:null;
  if(!pattern?.test(calendarValue))return '';
  const [year,month=1,day=1]=calendarValue.split('-').map(Number);
  return year>=1000&&year<=9999&&month>=1&&month<=12&&day>=1&&day<=new Date(Date.UTC(year,month,0)).getUTCDate()?calendarValue:'';
 }
 function validCalendar(){return calendarMode==='all'||!!calendarFilter()}
 function syncToday(){
  if(calendarMode!=='today'||!available())return;
  const today=calendarDate(new Date().toISOString(),config.default_timezone||'America/Los_Angeles');
  if(calendarValue!==today){calendarValue=today;listPage=1;if(polling){jobsGeneration++;jobsReloadRequested=true}renderCalendarControls();if(mounted&&mode==='saved'&&jobsRows)renderJobRows()}
 }
 function jobQuery(){return {limit:pageSize,offset:(listPage-1)*pageSize,search,status:filter,...(calendarFilter()?{calendar:calendarFilter()}:{}),sort:calendarSort}}
 function acceptJobs(value,query){jobs=value.jobs||[];jobsMeta=isPage(value)?{total:value.total,offset:value.offset}:null;jobsQueryKey=JSON.stringify(query);jobsError='';applyEngine(value.scheduler)}
 function queueJobs(){jobsGeneration++;jobsReloadRequested=true;if(!polling)void refreshJobs()}
 function reconcileCreate(){if(creating||!review?.attempted)return;const known=jobs.find(value=>value.request_id===review.requestId);if(known){saved='Đã ghi nhận lịch #'+known.id+' cho '+review.pageName+'.';review=null;draft.caption='';draft.scheduledLocal='';formError='';renderForm()}}
 async function load(){
  if(loading||!mounted)return;
  const needsConfig=!config||!!configError;
  const sequence=++loadGeneration,assetSequence=++assetsGeneration,catalogSequence=++catalogGeneration;
  ++jobsGeneration;loading=true;configError='';renderShell();
  const calls=[];
  if(needsConfig)calls.push(['config',()=>read(api.tokenReelConfig,'config')]);
  if(mode==='create'&&!catalogLoaded)calls.push(['catalog',()=>read(catalogWindow,'catalog')]);
  if(mode==='create'&&!assetsLoaded)calls.push(['assets',()=>read(assetWindow,'assets')]);
  const results=await Promise.allSettled(calls.map(([,call])=>call()));
  if(sequence!==loadGeneration)return;
  results.forEach((result,index)=>{
   const key=calls[index][0];if(key==='catalog'&&catalogSequence!==catalogGeneration||key==='assets'&&assetSequence!==assetsGeneration)return;if(result.status==='rejected'){if(result.reason?.name==='AbortError')return;const message=safeMessage(result.reason);if(key==='config')configError='Server hiện chưa sẵn sàng cho lịch Reel bằng token. '+message;else if(key==='catalog')catalogError=message;else assetsError=message;return}
   const value=result.value;
   if(key==='config'){if(!Number.isFinite(value.max_video_bytes)||value.max_video_bytes<=0||!Array.isArray(value.video_extensions)||typeof value.scheduler_running!=='boolean'){configError='Chưa đọc được cấu hình lịch Reel hợp lệ từ server.';return}config=value;if(!draft.timezone)draft.timezone=value.default_timezone||'America/Los_Angeles';syncToday()}
   else if(key==='catalog'){if(catalogSequence===catalogGeneration){catalog=value.items||[];catalogTotal=isPage(value)?value.total:null;if(isPage(value))catalogOffset=value.offset;catalogLoaded=true;catalogError=''}}else if(key==='assets'){if(assetSequence===assetsGeneration){assets=value.assets||[];assetTotal=isPage(value)?value.total:null;if(isPage(value))assetOffset=value.offset;assetsLoaded=true;assetsError=''}}
  });
  if(mode==='saved'&&available()&&validCalendar()){
   const query=jobQuery(),requestSequence=jobsGeneration;
   try{const value=await read(signal=>api.tokenReelJobs(query,signal));if(sequence!==loadGeneration)return;if(requestSequence===jobsGeneration)acceptJobs(value,query);else jobsReloadRequested=true}
   catch(reason){if(sequence!==loadGeneration)return;if(requestSequence!==jobsGeneration)jobsReloadRequested=true;else if(reason.name!=='AbortError')jobsError=safeMessage(reason)}
  }
  loading=false;initialized=available();if(mounted){renderShell();reconcileCreate()}
  if(mounted&&mode==='saved'&&jobsReloadRequested)void refreshJobs()
 }
 function applyEngine(value){if(config&&value){if(typeof value.scheduler_running==='boolean')config.scheduler_running=value.scheduler_running;if(Number.isFinite(value.active_count))config.active_count=value.active_count}}
 async function refreshJobs(){
  syncToday();
  if(!mounted||!available()||polling||loading||mode==='saved'&&!validCalendar())return;polling=true;const sequence=++jobsGeneration;activePoll=sequence;
  jobsReloadRequested=false;const query=jobQuery(),savedMode=mode==='saved';
  if(jobsRefresh)jobsRefresh.disabled=true;
  try{
   if(!savedMode&&!review?.attempted)return;
   const results=await Promise.allSettled([read(signal=>api.tokenReelJobs(savedMode?query:{limit:1,request_id:review.requestId},signal)),...(savedMode&&jobsMeta&&detailId!==null?[read(signal=>api.tokenReelDetail(detailId,signal))]:[])]);
   if(!mounted||sequence!==jobsGeneration)return;
   const result=results[0];if(result.status==='fulfilled'){if(savedMode)acceptJobs(result.value,query);else jobs=result.value.jobs||[];reconcileCreate();if(results[1]?.status==='fulfilled'&&String(results[1].value.job?.id)===String(detailId))detailRecord=results[1].value.job;
    if(savedMode&&jobsMeta){const last=Math.max(1,Math.ceil(jobsMeta.total/pageSize));if(listPage>last){listPage=last;jobsReloadRequested=true}}
    if(savedMode&&!jobsMeta){const value=await read(signal=>api.tokenReelAssets({limit:100},signal));if(sequence!==jobsGeneration)return;assets=value.assets||[]}
   }else if(result.reason?.name!=='AbortError')jobsError=safeMessage(result.reason);
   renderJobs();
  }catch(reason){if(reason.name!=='AbortError'&&mounted&&sequence===jobsGeneration){jobsError=safeMessage(reason);renderJobs()}}
  finally{if(activePoll===sequence){activePoll=0;polling=false;if(mounted&&jobsRefresh)jobsRefresh.disabled=false;if(mounted&&jobsReloadRequested)void refreshJobs()}}
 }
 async function refreshCatalog(){const sequence=++catalogGeneration;try{const value=await read(catalogWindow,'catalog');if(!mounted||sequence!==catalogGeneration)return;selectedPageRecord=selectedPage()||selectedPageRecord;catalog=value.items||[];catalogTotal=isPage(value)?value.total:null;if(isPage(value))catalogOffset=value.offset;catalogLoaded=true;catalogError='';renderForm()}catch(reason){if(mounted&&sequence===catalogGeneration&&reason.name!=='AbortError'){catalogError=safeMessage(reason);renderForm()}}}
 async function refreshAssets(){
  const sequence=++assetsGeneration;
  try{const value=await read(assetWindow,'assets');if(sequence!==assetsGeneration||!mounted)return;selectedAssetRecord=selectedAsset()||selectedAssetRecord;assets=value.assets||[];assetTotal=isPage(value)?value.total:null;if(isPage(value))assetOffset=value.offset;assetsLoaded=true;assetsError='';renderForm()}
  catch(reason){if(sequence===assetsGeneration&&reason.name!=='AbortError'&&mounted){assetsError=safeMessage(reason);renderForm();renderJobs()}}
 }
 function renderShell(){
  pauseVideos();root.className='token-reels token-reels--'+mode;root.replaceChildren();
  if(loading&&!config){root.append(skeleton(5));return}
  if(configError||!config){root.append(el('div',{class:'token-reels-error',role:'status'},configError||'Chưa đọc được cấu hình lịch Reel.'),button('Đọc lại cấu hình',()=>void load(),{iconName:'refresh'}));return}
  if(mode==='saved'){root.append(jobsHost);renderJobs()}else{root.append(formHost);renderForm()}
 }
 function renderForm(){
  if(!mounted||mode!=='create')return;
  const active=formHost.contains(document.activeElement)?document.activeElement:null,activeLabel=active?.getAttribute('aria-label'),position=active?.selectionStart;
  if(activeLabel)queueMicrotask(()=>{const replacement=[...formHost.querySelectorAll('[aria-label]')].find(node=>node.getAttribute('aria-label')===activeLabel);if(replacement){replacement.focus({preventScroll:true});if(Number.isInteger(position)&&replacement.setSelectionRange)try{replacement.setSelectionRange(position,position)}catch{}}});
  formHost.replaceChildren();
  const form=el('form',{class:'token-reels-form'});formHost.append(form);
  if(saved)form.append(el('p',{class:'token-reels-saved',role:'status',text:saved}));
  if(formError)form.append(el('p',{class:'token-reels-error',role:'alert',text:formError}));
  if(review){renderReview(form);return}
  if(selectedPage()&&!canPublishPage(selectedPage())){draft.pageId='';draft.connectionId='';selectedPageRecord=null}
  const pageSelect=el('select',{required:true,'aria-label':'Page đăng Reel',disabled:creating||uploading,onChange:event=>{draft.pageId=event.target.value;selectedPageRecord=selectedPage();draft.connectionId='';clearReview();renderForm()}},el('option',{value:'',text:'Chọn đúng Page'}));
  const pageOptions=catalog.filter(canPublishPage);if(canPublishPage(selectedPage())&&!pageOptions.some(page=>String(page.id)===draft.pageId))pageOptions.unshift(selectedPage());
  for(const page of pageOptions)pageSelect.append(el('option',{value:page.id,text:(page.name||'Page')+' · '+page.id}));pageSelect.value=draft.pageId;
  const source=el('select',{required:true,'aria-label':'Nguồn token đăng Reel',disabled:!draft.pageId||creating||uploading,onChange:event=>{draft.connectionId=event.target.value;clearReview()}},el('option',{value:'',text:'Chọn kết nối cho Page này'}));
  for(const connection of linkedSources())source.append(el('option',{value:connection.id,text:connection.label+' · #'+connection.id}));source.value=draft.connectionId;
  form.append(el('div',{class:'token-reels-pair'},el('div',{},optionSearch('catalog'),el('label',{},'Page',pageSelect),optionPager('catalog')),el('label',{},'Nguồn token',source)));
  if(!draft.pageId)form.append(el('p',{class:'field-hint',text:'Chọn đúng Page và nguồn token bên trên.'}));
  if(catalogError)form.append(el('p',{class:'token-reels-error',text:catalogError}));
  if(draft.pageId)form.append(el('p',{class:'token-reels-source-note',text:'Meta Page ID '+draft.pageId+'. Chỉ chọn kết nối đang liên kết với Page này; quyền đăng được kiểm tra lại ở server.'}));
  form.append(renderVideo());
  const caption=el('textarea',{rows:4,maxlength:2200,value:draft.caption,'aria-label':'Caption Reel',disabled:uploading,onInput:event=>{draft.caption=event.target.value;clearReview()}});
  const datePreview=el('p',{class:'field-hint token-reels-date-preview','aria-live':'polite',text:localDateTime(draft.scheduledLocal)+' · '+draft.timezone});
  const date=el('input',{type:'datetime-local',lang:'vi',required:true,value:draft.scheduledLocal,'aria-label':'Thời gian đăng Reel',disabled:uploading,onInput:event=>{draft.scheduledLocal=event.target.value;datePreview.textContent=localDateTime(draft.scheduledLocal)+' · '+draft.timezone;clearReview()}});
  const zones=[...new Set([draft.timezone,config.default_timezone,...(config.timezones||[]),'America/Los_Angeles','Asia/Ho_Chi_Minh','UTC'].filter(Boolean))];
  const timezone=el('select',{'aria-label':'Múi giờ lịch Reel',disabled:uploading,onChange:event=>{draft.timezone=event.target.value;datePreview.textContent=localDateTime(draft.scheduledLocal)+' · '+draft.timezone;clearReview()}},zones.map(value=>el('option',{value,text:value})));timezone.value=draft.timezone;
  const fold=el('select',{'aria-label':'Lần xuất hiện giờ DST',disabled:uploading,onChange:event=>{draft.fold=event.target.value;clearReview()}},el('option',{value:'0',text:'Lần đầu'}),el('option',{value:'1',text:'Lần thứ hai'}));fold.value=draft.fold;
  form.append(el('label',{},'Caption',caption),el('div',{class:'token-reels-pair'},el('label',{},'Ngày/tháng/năm và giờ (dd/MM/yyyy HH:mm)',date),el('label',{},'Múi giờ',timezone)),datePreview,el('details',{class:'token-reels-dst'},el('summary',{text:'Nếu giờ bị lặp khi đổi giờ mùa hè (DST)'}),el('label',{},'Chọn lần xuất hiện của giờ đã nhập',fold)),el('p',{class:'field-hint',text:'Giờ được server kiểm tra theo múi giờ IANA. Lịch đến hạn chỉ chạy khi nguồn và tài nguyên sẵn sàng. Giới hạn đăng áp dụng theo từng Page.'}),el('button',{type:'submit',class:'button primary',disabled:uploading||!available(),text:'Xem lại lịch'}));
  form.addEventListener('submit',event=>{event.preventDefault();freezeReview()});
 }
 function optionSearch(kind){const page=kind==='catalog',label=page?'Tìm Page đăng Reel':'Tìm video đã lưu';return el('label',{class:'token-reels-option-search'},label,el('input',{type:'search',value:page?catalogSearch:assetSearch,'aria-label':label,disabled:uploading,onInput:event=>{if(page){catalogSearch=event.target.value;catalogOffset=0;catalogGeneration++}else{assetSearch=event.target.value;assetOffset=0;assetsGeneration++}clearTimeout(optionTimers[kind]);optionTimers[kind]=setTimeout(()=>void(page?refreshCatalog():refreshAssets()),250)}}))}
 function optionPager(kind){const page=kind==='catalog',offset=page?catalogOffset:assetOffset,total=page?catalogTotal:assetTotal,values=page?catalog:assets,label=page?'Page đăng Reel':'Video chọn lịch';if(total===null)return null;const go=direction=>{if(page){catalogOffset=Math.max(0,catalogOffset+direction*optionSize);void refreshCatalog()}else{assetOffset=Math.max(0,assetOffset+direction*optionSize);void refreshAssets()}};return el('div',{class:'token-reels-options-pager'},el('small',{text:(total?offset+1:0)+'–'+Math.min(offset+values.length,total)+' / '+total}),button('Trước',()=>go(-1),{small:true,disabled:uploading||offset===0,'aria-label':'Trang trước · '+label}),button('Sau',()=>go(1),{small:true,disabled:uploading||offset+optionSize>=total,'aria-label':'Trang sau · '+label}))}
 function renderVideo(){
  const section=el('section',{class:'token-reels-video'},el('h3',{text:'Video của lịch'}));
  const assetSelect=el('select',{'aria-label':'Video đã lưu trên manager',disabled:uploading,onChange:event=>{draft.assetId=event.target.value;selectedAssetRecord=selectedAsset();draft.file=null;clearPreview();clearReview();uploadError='';renderForm()}},el('option',{value:'',text:'Chọn video đã tải lên manager'}));
  const assetOptions=[...assets];if(selectedAsset()&&!assetOptions.some(asset=>String(asset.id)===draft.assetId))assetOptions.unshift(selectedAsset());
  for(const asset of assetOptions)assetSelect.append(el('option',{value:asset.id,text:asset.name+' · '+fileSize(asset.size)}));assetSelect.value=draft.assetId;
  const input=el('input',{type:'file',accept:config.video_extensions.join(','),'aria-label':'Chọn video Reel từ máy',disabled:uploading,onChange:event=>{
   const file=event.target.files?.[0];if(!file)return;const extension='.'+(file.name.split('.').at(-1)||'').toLowerCase();
   if(!config.video_extensions.includes(extension)||file.size===0||file.size>config.max_video_bytes){uploadError='Chọn video '+config.video_extensions.join(', ')+' không quá '+fileSize(config.max_video_bytes)+'.';renderForm();return}
   clearPreview();draft.file=file;draft.assetId='';localPreview=URL.createObjectURL(file);clearReview();uploadError='';renderForm();
  }});
  section.append(el('div',{class:'token-reels-pair'},el('div',{},optionSearch('assets'),el('label',{},'Video đã có',assetSelect),optionPager('assets')),el('label',{},'Hoặc chọn tệp mới',input)),el('p',{class:'field-hint',text:'Giới hạn tải lên ứng dụng: '+fileSize(config.max_video_bytes)+'. Chọn tệp chưa tự tải lên hoặc tạo lịch.'}));
  if(draft.file)section.append(el('div',{class:'token-reels-upload'},button(uploading?'Đang tải lên manager…':'Tải video lên manager',()=>void upload(),{disabled:uploading,iconName:'plus'}),el('span',{text:draft.file.name+' · '+fileSize(draft.file.size)})));
  if(uploading)section.append(el('div',{class:'token-reels-progress',role:'status'},el('progress',{}),el('span',{text:'Đang truyền tệp vào manager; chưa gửi lên Meta.'}),button('Hủy tải lên',()=>uploadController?.abort(),{small:true,style:'ghost'})));
  if(uploadError)section.append(el('p',{class:'token-reels-error',role:'alert',text:uploadError}));
  if(assetsError)section.append(el('p',{class:'field-hint',text:assetsError}));
  section.append(button('Đọc lại video đã lưu',()=>void refreshAssets(),{small:true,style:'ghost',disabled:uploading,iconName:'refresh'}));
  const asset=selectedAsset(),preview=draft.file?localPreview:assetUrl(asset?.preview_url,asset?.id);
  if(preview)section.append(el('video',{class:'token-reels-preview',src:preview,controls:true,preload:'metadata','aria-label':'Xem trước video Reel'}),el('p',{class:'field-hint',text:draft.file?'Bản xem trước từ máy; cần tải lên manager trước khi đặt lịch.':'Bản xem trước của video đã lưu trên manager.'}));return section;
 }
 async function upload(){
  if(uploading||!draft.file)return;const file=draft.file;assetsGeneration++;uploading=true;uploadError='';uploadController=new AbortController();renderForm();
  try{const value=await api.tokenReelUpload(file,uploadController.signal);if(!value.asset?.id)throw new Error('Invalid upload result');assetsGeneration++;assets=[value.asset,...assets.filter(asset=>String(asset.id)!==String(value.asset.id))].slice(0,optionSize);selectedAssetRecord=value.asset;draft.assetId=String(value.asset.id);draft.file=null;clearPreview();saved='Video đã lưu trên manager. Chưa tạo lịch và chưa gửi lên Meta.'}
  catch(reason){uploadError=reason.name==='AbortError'?'Đã dừng tải tệp. Đọc lại danh sách video trước khi tải lại.':safeMessage(reason)+' Không tự tải lại; bạn có thể đọc lại video đã lưu.'}
  finally{uploading=false;uploadController=null;if(mounted)renderForm()}
 }
 function freezeReview(){
  const page=selectedPage(),source=linkedSources(page).find(value=>String(value.id)===draft.connectionId),asset=selectedAsset();
  if(page&&!canPublishPage(page)){formError=PAGE_INFORMATION_REQUIRED;renderForm();return}
  if(!page||!source||!asset||draft.file||!draft.scheduledLocal){formError='Chọn đúng Page, nguồn liên kết, video đã tải lên manager và thời gian đăng.';renderForm();return}
  review={requestId:crypto.randomUUID(),pageId:String(page.id),pageName:page.name||'Page '+page.id,connectionId:Number(source.id),connectionLabel:source.label,assetId:String(asset.id),assetName:asset.name,caption:draft.caption,scheduledLocal:draft.scheduledLocal,timezone:draft.timezone,fold:Number(draft.fold),attempted:false};formError='';saved='';renderForm();const title=formHost.querySelector('.token-reels-review h3');if(title){title.tabIndex=-1;title.focus({preventScroll:true})}formHost.scrollIntoView({block:'start'});
 }
 function renderReview(form){
  const summary=el('section',{class:'token-reels-review'},el('h3',{text:'Đăng công khai lên Page đã chọn'}),el('dl',{},[['Page',review.pageName],['Meta Page ID',review.pageId],['Nguồn',review.connectionLabel+' · #'+review.connectionId],['Video',review.assetName],['Caption',review.caption||'(Không có caption)'],['Giờ đăng',localDateTime(review.scheduledLocal)],['Múi giờ',review.timezone],['Giờ DST',review.fold===1?'Lần thứ hai':'Lần đầu']].flatMap(([name,value])=>[el('dt',{text:name}),el('dd',{text:value})])),el('p',{text:'Bấm Đặt lịch sẽ lưu lịch và cho phép server đăng công khai Reel này khi đến hạn. Đây là lịch bằng token, không sử dụng Chrome profile hay hàng đợi Chrome.'}));
  form.append(summary,el('div',{class:'token-reels-review-actions'},!review.attempted?button('Quay lại chỉnh sửa',()=>{review=null;formError='';renderForm()},{disabled:creating}):button('Kiểm tra danh sách lịch',()=>void refreshJobs(),{disabled:creating,iconName:'refresh'}),button(creating?'Đang ghi lịch…':review.attempted?'Thử xác nhận cùng yêu cầu':'Đặt lịch',()=>void create(review.attempted),{style:'primary',disabled:creating,'aria-label':review.attempted?'Thử xác nhận cùng yêu cầu lịch Reel':'Đặt lịch Reel bằng token'})));
  if(review.attempted)form.append(el('p',{class:'field-hint',text:'Yêu cầu đã được gửi một lần. Khi bạn bấm thử xác nhận, manager dùng lại cùng mã yêu cầu và nội dung để tránh tạo hai lịch. Không tự động gửi lại.'}));
 }
 async function create(explicitRetry=false){
  if(creating||!review||review.attempted&&!explicitRetry)return;const frozen=review;frozen.attempted=true;creating=true;jobsGeneration++;formError='';renderForm();
  try{const value=await api.tokenReelCreate({request_id:frozen.requestId,page_id:frozen.pageId,connection_id:frozen.connectionId,asset_id:frozen.assetId,caption:frozen.caption,scheduled_local:frozen.scheduledLocal,timezone:frozen.timezone,fold:frozen.fold});if(!value.job||value.job.request_id!==frozen.requestId)throw new Error('Invalid created job');jobsGeneration++;mergeJob(value.job);saved='Đã ghi nhận lịch #'+value.job.id+' cho '+frozen.pageName+'.';review=null;draft.caption='';draft.scheduledLocal=''}
  catch(reason){if(reason.code==='page_information_required'){frozen.attempted=false;review=null;catalog=catalog.filter(page=>String(page.id)!==frozen.pageId);if(String(selectedPageRecord?.id)===frozen.pageId)selectedPageRecord=null;draft.pageId='';draft.connectionId='';formError=safeMessage(reason)+' Lịch chưa được tạo; chỉnh sửa Page trước khi chọn lại.';void refreshCatalog()}else if([400,401,403,404,415,422].includes(reason.status)){frozen.attempted=false;formError=safeMessage(reason)+' Lịch chưa được tạo; quay lại chỉnh sửa trước khi xác nhận.'}else formError=safeMessage(reason)+' Chưa tạo yêu cầu mới; đọc lại danh sách hoặc xác nhận lại cùng yêu cầu.'}
  finally{creating=false;reconcileCreate();if(mounted){renderForm();renderJobs()}}
 }
 function calendarChanged(){listPage=1;if(!validCalendar()){jobsGeneration++;jobsReloadRequested=false}else if(jobsMeta)queueJobs();renderJobRows()}
 function renderCalendarControls(){
  if(!calendarControls)return;
  const period=el('select',{'aria-label':'Xem lịch theo ngày tháng năm',onChange:event=>{calendarMode=event.target.value;const today=calendarDate(new Date().toISOString(),config.default_timezone||'America/Los_Angeles');calendarValue=['today','day'].includes(calendarMode)?today:calendarMode==='month'?today.slice(0,7):calendarMode==='year'?today.slice(0,4):'';renderCalendarControls();calendarChanged()}},[['today','Hôm nay'],['all','Tất cả ngày'],['day','Theo ngày'],['month','Theo tháng'],['year','Theo năm']].map(([value,text])=>el('option',{value,text})));period.value=calendarMode;
  const sort=el('select',{'aria-label':'Sắp xếp lịch theo thời gian',onChange:event=>{calendarSort=event.target.value;calendarChanged()}},[['recent','Mới lưu trước'],['scheduled_asc','Giờ đăng tăng dần'],['scheduled_desc','Giờ đăng giảm dần']].map(([value,text])=>el('option',{value,text})));sort.value=calendarSort;
  const nodes=[el('label',{},'Ngày / tháng / năm',period)];
  if(calendarMode==='today')nodes[0].append(el('small',{class:'token-reels-today',text:'Hôm nay: '+calendarLabel(calendarValue)+' · '+(config.default_timezone||'America/Los_Angeles')}));
  else if(calendarMode!=='all'){
   const label=calendarMode==='day'?'Ngày đăng (dd/MM/yyyy)':calendarMode==='month'?'Tháng đăng (MM/yyyy)':'Năm đăng (yyyy)';
   const selected=el('small',{class:'token-reels-calendar-preview','aria-live':'polite',text:calendarFilter()?'Đang lọc: '+calendarLabel(calendarFilter()):'Chưa chọn kỳ lọc'});
   const input=el('input',{type:calendarMode==='year'?'number':calendarMode==='month'?'month':'date',lang:'vi',value:calendarValue,...(calendarMode==='year'?{min:1000,max:9999,step:1}:calendarMode==='month'?{min:'1000-01',max:'9999-12'}:{min:'1000-01-01',max:'9999-12-31'}),'aria-label':label,onInput:event=>{calendarValue=event.target.value;selected.textContent=calendarFilter()?'Đang lọc: '+calendarLabel(calendarFilter()):'Chưa chọn kỳ lọc hợp lệ';calendarChanged()}});
   nodes.push(el('label',{},label,input,selected));
  }
  nodes.push(el('label',{},'Thứ tự lịch',sort),el('p',{class:'field-hint',text:'Lọc theo ngày tại múi giờ của từng lịch. Các nhóm bên dưới thuộc trang đang xem; không thay đổi giờ đã lưu.'}));
  calendarControls.replaceChildren(...nodes);
 }
 function renderJobs(){
  if(!mounted||mode!=='saved')return;
  if(!jobsRows){
   jobsRefresh=button('Làm mới lịch',()=>void refreshJobs(),{small:true,iconName:'refresh',disabled:polling});
   const searchInput=el('input',{type:'search',value:search,placeholder:'Tìm Page, nguồn hoặc video…','aria-label':'Tìm lịch Reel token',onInput:event=>{search=event.target.value;listPage=1;if(jobsMeta){jobsGeneration++;clearTimeout(searchTimer);searchTimer=setTimeout(queueJobs,250)}renderJobRows()}}),status=el('select',{'aria-label':'Lọc lịch Reel token',onChange:event=>{filter=event.target.value;listPage=1;if(jobsMeta)queueJobs();renderJobRows()}},el('option',{value:'all',text:'Tất cả trạng thái'}),el('option',{value:'pending',text:'Đang chờ / đang chạy'}),el('option',{value:'attention',text:'Cần kiểm tra'}),el('option',{value:'completed',text:'Đã đăng'}),el('option',{value:'cancelled',text:'Đã hủy'}));status.value=filter;
   jobsErrorHost=el('div',{class:'token-reels-list-notices'});jobsRows=el('div',{id:'token-reels-job-rows'});jobsPager=el('nav',{class:'token-reels-pagination','aria-label':'Phân trang lịch Reel token'});
   calendarControls=el('div',{class:'token-reels-calendar-controls'});renderCalendarControls();
   jobsHost.append(el('div',{class:'token-reels-filters'},searchInput,status,jobsRefresh),calendarControls,jobsErrorHost,jobsRows,jobsPager);
  }
  jobsRefresh.disabled=polling;
  jobsErrorHost.replaceChildren();
  if(jobsError)jobsErrorHost.append(el('p',{class:'token-reels-error',role:'status',text:jobsError+(jobs.length?' Đang hiển thị dữ liệu đã đọc trước đó.':'')}));
  if(assetsError)jobsErrorHost.append(el('p',{class:'token-reels-error',role:'status',text:'Chưa đọc lại được danh sách video. '+assetsError}));
  renderJobRows();renderJobDetails();
 }
 function releaseCard(id){const item=jobCards.get(id);if(!item)return;pauseVideos(item.card);for(const video of item.card.querySelectorAll('video')){video.removeAttribute('src');video.load()}item.card.remove();jobCards.delete(id)}
 function cardFor(job){
  const id=String(job.id);let item=jobCards.get(id);
  if(!item){
   const card=el('article',{class:'panel token-reels-job','aria-label':'Lịch Reel token #'+id,onClick:event=>{if(!(event.target instanceof Element)||event.target.closest('video,button,a,input,select,textarea,summary,details')||window.getSelection()?.toString())return;showJobDetails(id)}}),title=button('',()=>showJobDetails(id),{style:'ghost',class:'token-reels-card-title','aria-label':'Xem chi tiết lịch Reel token #'+id}),caption=el('p',{class:'token-reels-card-caption'}),media=el('div',{class:'token-reels-job-media'});
   const time=el('p',{class:'token-reels-card-time'});
   card.append(el('h3',{},title),caption,time,media);
   item={card,title,caption,time,media,preview:null,previewUrl:null,signature:null};jobCards.set(id,item);
  }
  const signature=JSON.stringify([job.page_name,job.page_id,job.caption,job.scheduled_at,job.timezone,job.publish_mode,job.approve_by,continuousReview(job)]);
  if(signature!==item.signature){
   item.signature=signature;item.title.textContent=job.page_name||'Page '+job.page_id;item.caption.textContent=job.caption||'(Không có caption)';
   item.time.replaceChildren(job.publish_mode==='on_approval'?el('span',{text:postingTime(job)}):el('time',{datetime:job.scheduled_at,text:postingTime(job)}),el('small',{text:reviewTime(job)}));
  }
  const asset=job.asset&&String(job.asset.id)===String(job.asset_id)?job.asset:assets.find(value=>String(value.id)===String(job.asset_id)),preview=asset?assetUrl(asset.preview_url,job.asset_id):null;
  if(preview!==item.previewUrl||!item.media.childElementCount){
   pauseVideos(item.media);item.media.replaceChildren();item.preview=null;item.previewUrl=preview;
   if(preview){
    const warning=el('div',{class:'token-reels-media-unavailable',role:'status',hidden:true});
    const video=el('video',{class:'token-reels-job-preview',src:preview,controls:true,playsinline:true,preload:'metadata','aria-label':'Xem video lịch Reel token #'+id,onError:()=>{warning.hidden=false},onLoadedmetadata:()=>{warning.hidden=true}});
    warning.append(el('p',{text:'Không phát được video trên trình duyệt này. Lịch và tệp gốc vẫn được giữ.'}),button('Tải lại video',()=>{warning.hidden=true;video.load()},{small:true,style:'ghost','aria-label':'Tải lại video lịch Reel token #'+id}));
    item.preview=video;item.media.append(video,warning);
   }else item.media.append(el('div',{class:'token-reels-media-empty'},icon('eye',27),el('p',{class:'token-reels-media-unavailable',text:asset?'Video này chưa có địa chỉ xem trước hợp lệ.':'Chưa có video trong kho cho lịch này.'}),el('small',{text:'Bạn vẫn có thể xem thông tin và trạng thái lịch.'})));
  }
  return item;
 }
 function showJobDetails(id){
  if(!mounted||mode!=='saved'||!jobs.some(job=>String(job.id)===String(id)))return;
  detailId=String(id);detailRecord=jobs.find(job=>String(job.id)===detailId);detailSignature=null;pauseVideos();renderJobDetails();if(!detailDialog.open)openDialog(detailDialog);
 }
 function renderJobDetails(){
  if(detailId===null)return;
  const selectedId=detailId,job=jobs.find(value=>String(value.id)===selectedId)||(String(detailRecord?.id)===selectedId?detailRecord:null),signature=JSON.stringify([job,jobBusy.has(selectedId),jobsError]);if(signature===detailSignature)return;detailSignature=signature;
  const focused=detailDialog.contains(document.activeElement)?document.activeElement?.getAttribute('aria-label'):null;
  const close=button('',()=>detailDialog.close(),{style:'ghost','aria-label':'Đóng chi tiết lịch Reel'});close.replaceChildren(icon('x',20));
  const heading=el('div',{class:'dialog-heading'},el('div',{},el('h2',{id:'token-reels-detail-title',text:'Chi tiết lịch Reel #'+detailId}),el('p',{text:job?.page_name||'Thông tin lịch đã lưu'})),close),body=el('div',{class:'token-reels-detail-body'});detailDialog.replaceChildren(heading,body);
  if(jobsError)body.append(el('p',{class:'token-reels-error',role:'status',text:jobsError+' Đang hiển thị thông tin đã đọc trước đó.'}));
  if(!job){body.append(empty('Lịch không còn trong danh sách','Đóng chi tiết và làm mới danh sách để kiểm tra lại.'));return}
  const [label,tone]=STATES[job.status]||['Chưa xác định','neutral'];body.append(el('div',{class:'token-reels-detail-status'},badge(label,tone)));
  const rows=[['Page',job.page_name||'Page '+job.page_id],['Meta Page ID',job.page_id],['Lịch',String(job.id)],['Nguồn token',job.connection_label||'#'+job.connection_id],['Mã kết nối',String(job.connection_id)],['Video',job.asset_name||'Video đã lưu'],['Caption',job.caption||'(Không có caption)'],['Giờ đăng',postingTime(job)],['Múi giờ',job.timezone],job.publish_mode==='on_approval'?['Hạn duyệt',dateTime(job.approve_by,job.timezone)]:['Giờ đã nhập',localDateTime(job.scheduled_local)],['Tiến trình',STATES[job.step]?.[0]||label],['Thông báo',job.message||'Chưa có thông báo mới.'],['Cập nhật',dateTime(job.updated_at,job.timezone)]];
  if(job.video_id)rows.push(['Meta Video ID',job.video_id]);
  if(continuousReview(job)){
   const deadline=rows.findIndex(row=>row[0]==='Hạn duyệt');if(deadline>=0)rows.splice(deadline,1);
   rows.push(['Duyệt đăng','Sau giờ hẹn, nhắc mỗi 5 phút đến khi bạn quyết định; duyệt muộn sẽ đăng ngay.']);
  }
  body.append(el('dl',{class:'token-reels-detail-grid'},rows.flatMap(([name,value])=>[el('dt',{text:name}),el('dd',{text:value})])));
  if(['needs_user','needs_review'].includes(job.status))body.append(el('p',{class:'token-reels-job-note',text:'Xem trạng thái và quyền nguồn trước khi tiếp tục. Khi chưa rõ kết quả, chỉ xác minh; không tạo lịch trùng để đăng lại.'}));
  const actions=el('div',{class:'token-reels-job-actions'}),disabled=jobBusy.has(detailId);
  for(const [kind,allowed,title] of [['cancel',job.can_cancel,'Hủy lịch'],['resume',job.can_resume,'Tiếp tục'],['verify',job.can_verify,'Xác minh trạng thái']])if(allowed===true)actions.append(button(title,()=>void act(selectedId,kind),{small:true,disabled,'aria-label':title+' Reel token #'+selectedId,iconName:kind==='verify'?'shield':kind==='cancel'?'x':'refresh'}));
  const published=reelUrl(job.published_url);if(job.status==='completed'&&published)actions.append(el('a',{class:'button small',href:published,target:'_blank',rel:'noopener noreferrer'},icon('external',13),'Mở Reel đã đăng'));
  body.append(actions);
  if(focused){const match=[...detailDialog.querySelectorAll('[aria-label]')].find(node=>node.getAttribute('aria-label')===focused);match?.focus({preventScroll:true})}
 }
 function renderJobRows(){
  if(!mounted||mode!=='saved'||!jobsRows)return;const target=jobsRows;
  if(!validCalendar()){for(const id of jobCards.keys())releaseCard(id);target.replaceChildren(empty('Chọn kỳ lịch hợp lệ','Nhập đủ ngày/tháng/năm, tháng/năm hoặc năm có 4 chữ số (1000–9999).'));jobsPager.replaceChildren(el('span',{text:'Chưa lọc lịch'}));return}
  let values=jobsMeta?jobs:jobs.filter(job=>[job.page_name,job.page_id,job.connection_label,job.asset_name,job.id].join(' ').toLowerCase().includes(search.toLowerCase())&&(filter==='all'||filter==='attention'?filter==='all'||['needs_user','needs_review'].includes(job.status):filter==='pending'?!['completed','cancelled','needs_user','needs_review'].includes(job.status):job.status===filter));
  if(!jobsMeta){const calendar=calendarFilter();if(calendar)values=values.filter(job=>calendarDate(job.scheduled_at,job.timezone).startsWith(calendar));if(calendarSort!=='recent')values=[...values].sort((a,b)=>(Date.parse(a.scheduled_at)-Date.parse(b.scheduled_at))*(calendarSort==='scheduled_desc'?-1:1)||Number(b.id)-Number(a.id))}
  const total=jobsMeta?.total??values.length,totalPages=Math.max(1,Math.ceil(total/pageSize));listPage=Math.max(1,Math.min(listPage,totalPages));const start=(listPage-1)*pageSize,visible=jobsMeta?values:values.slice(start,start+pageSize),ids=new Set(visible.map(job=>String(job.id)));
  if(jobsMeta&&jobsQueryKey!==JSON.stringify(jobQuery())){for(const id of jobCards.keys())releaseCard(id);target.replaceChildren(jobsError?empty('Chưa tải được trang lịch','Bấm Làm mới lịch để thử lại.'):skeleton(3));return}
  for(const id of jobCards.keys())if(!ids.has(id))releaseCard(id);
  const desired=[];
  if(!visible.length){const filtered=!!search||filter!=='all'||!!calendarFilter();desired.push(empty(jobs.length||filtered?'Không có lịch phù hợp':'Chưa có lịch Reel bằng token',jobs.length||filtered?'Đổi bộ lọc để xem lịch khác.':'Tạo một lịch mới để xem video và theo dõi tiến trình tại đây.'))}
  else {
   const groups=[],width=calendarMode==='year'?4:calendarMode==='month'?7:10;
   for(const job of visible){const date=calendarDate(job.scheduled_at,job.timezone),key=date?date.slice(0,width):'unknown';if(groups.at(-1)?.key!==key)groups.push({key,items:[]});groups.at(-1).items.push(job)}
   for(const [index,{key,items}] of groups.entries()){
    const groupId=key+':'+index;
    let heading=[...target.children].find(node=>node.dataset.calendarGroup===groupId);
    if(!heading)heading=el('h3',{class:'token-reels-calendar-group','data-calendar-group':groupId});
    heading.textContent=(key==='unknown'?'Chưa xác định ngày':calendarLabel(key))+' · '+items.length+' lịch trên trang này';desired.push(heading);
    for(const job of items)desired.push(cardFor(job).card);
   }
  }
  for(const node of [...target.children])if(!desired.includes(node))node.remove();
  for(const [index,node] of desired.entries()){const current=target.children[index];if(current!==node)target.insertBefore(node,current||null)}
  jobsPager.replaceChildren(el('span',{text:total?'Hiển thị '+(start+1)+'–'+Math.min(start+pageSize,total)+' / '+total+' lịch':'0 lịch'}),el('div',{},button('Trang trước',()=>{listPage--;if(jobsMeta)queueJobs();renderJobRows()},{small:true,disabled:listPage===1,'aria-label':'Trang lịch trước'}),el('span',{text:listPage+' / '+totalPages}),button('Trang sau',()=>{listPage++;if(jobsMeta)queueJobs();renderJobRows()},{small:true,disabled:listPage===totalPages,'aria-label':'Trang lịch sau'})));
 }
 async function act(id,action){
  id=String(id);const job=jobs.find(value=>String(value.id)===id)||(String(detailRecord?.id)===id?detailRecord:null);if(!job||jobBusy.has(id)||job['can_'+action]!==true)return;jobBusy.add(id);jobsGeneration++;renderJobs();
  try{const value=await api.tokenReelAction(id,action);if(!value.job||String(value.job.id)!==id)throw new Error('Invalid job response');jobsGeneration++;mergeJob(value.job);jobsError=''}
  catch(reason){jobsError=safeMessage(reason)+' Không tự gửi lại thao tác; đọc lại danh sách để kiểm tra.'}
  finally{jobBusy.delete(id);if(mounted){renderJobs();if(jobsMeta)queueJobs()}}
 }
 function releaseMedia(){pauseVideos();for(const id of jobCards.keys())releaseCard(id);for(const video of root.querySelectorAll('video')){video.removeAttribute('src');video.load()}}
 function unmount(){releaseMedia();if(detailDialog.open)detailDialog.close();mounted=false;loadGeneration++;jobsGeneration++;assetsGeneration++;catalogGeneration++;clearTimeout(optionTimers.catalog);clearTimeout(optionTimers.assets);clearTimeout(searchTimer);cancelReads();if(poll)clearInterval(poll);poll=null;polling=false;activePoll=0;loading=false}
 function mount(target,info,nextMode='create'){
  if(nextMode!==mode){releaseMedia();cancelReads({keepConfig:true});loadGeneration++;jobsGeneration++;assetsGeneration++;catalogGeneration++;clearTimeout(optionTimers.catalog);clearTimeout(optionTimers.assets);clearTimeout(searchTimer);loading=false;polling=false;activePoll=0;jobsReloadRequested=false}mode=nextMode==='saved'?'saved':'create';if(mode!=='saved'&&detailDialog.open)detailDialog.close();
  if(info.token_reels!==true){unmount();root.replaceChildren(el('section',{class:'panel schedule-empty'},empty('Chưa có tính năng lên lịch bằng token','Server hiện chưa bật lịch Reel bằng token. Mở lại server đã cập nhật rồi làm mới trang để dùng. Lịch Chrome vẫn thuộc ứng dụng Chrome riêng.')));target.append(root);return}
  target.append(root);mounted=true;if(!poll)poll=setInterval(()=>{if(document.visibilityState==='visible')void refreshJobs()},15000);if((!initialized||mode==='create'&&(!catalogLoaded||!assetsLoaded))&&!loading)void load();else if(!loading){renderShell();if(mode==='saved')void refreshJobs();else void refreshCatalog()}
 }
 return {mount,unmount,isMounted:(target,nextMode='create')=>mounted&&root.parentNode===target&&mode===nextMode};
}
