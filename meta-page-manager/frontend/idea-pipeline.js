import {api,resourceUrl,readPageWindow,supportsApiFeature} from './api.js';
import {el,icon,button,badge,empty,skeleton,safeMessage,number,dateTime,toast} from './ui.js';
import {createPagination} from './pagination.js';
import {createProducerConnections} from './producer-connections.js';
import {createIdeaAutomation} from './idea-automation.js';
import {createAudioMixes} from './audio-mixes.js';
import {createProjectAssets} from './project-assets.js';
import {createTelegramApproval} from './telegram-approval.js';

const DIRECTORY='C:\\Users\\Admin\\Via\\media\\ideas',SOURCE='https://blueox179.com/campaign/zitat25',BLACK_SOURCE='https://blueox179.com/campaign/ge2610';
const STATUS={new:['Mới ghi nhận','neutral'],prepared:['Chờ upload áo','neutral'],planned:['Chưa tạo video','neutral'],queued:['Chờ agent nhận','neutral'],claimed:['Agent đang xử lý','warning'],submitting:['Đang upload áo','warning'],uncertain:['Cần đối chiếu','warning'],verified:['Đã xác minh','success'],ready:['Sẵn sàng','success'],blocked:['Đang bị chặn','warning'],failed:['Cần kiểm tra','danger'],error:['Cần kiểm tra','danger'],needs_review:['Cần kiểm tra','warning'],completed:['Đã đăng','success'],published:['Đã đăng','success']};
const JOBS={scheduled:'Đã lên lịch',starting:'Đang chuẩn bị',uploading:'Đang tải lên Meta',processing:'Meta đang xử lý',publishing:'Đang đăng',verifying:'Đang xác minh',completed:'Đã đăng',cancelled:'Đã hủy',waiting_page:'Chờ Page / nguồn',needs_user:'Cần kiểm tra',needs_review:'Cần đối chiếu'};
const text=value=>typeof value==='string'?value:'',list=value=>Array.isArray(value)?value:[],count=value=>Number.isInteger(value)&&value>=0?value:0;
function productUrl(value){try{const url=new URL(value);return url.protocol==='https:'&&url.hostname==='blueox179.com'&&!url.username&&!url.password&&!url.port&&!url.search&&!url.hash&&/^\/campaign\/[A-Za-z0-9_-]+\/?$/.test(url.pathname)?url.href:null}catch{return null}}
function assetUrl(asset){const id=String(asset?.id||'');if(!/^[a-zA-Z0-9_-]+$/.test(id))return null;const expected='/api/reels/assets/'+id+'/video';return asset.preview_url===expected?resourceUrl(expected):null}
function stateBadge(value){const [label,tone]=STATUS[value]||['Chưa xác định','neutral'];return badge(label,tone)}
function jobLabel(job){const gate=job.publication_approval;if(job.status==='scheduled'&&gate?.required===true&&gate.can_publish!==true)return {pending:'Chờ duyệt Telegram',missing:'Chờ yêu cầu duyệt',expired:'Quá hạn duyệt',rejected:'Đã từ chối đăng',binding_changed:'Nội dung đổi · cần duyệt lại'}[gate.state]||'Chờ đối chiếu duyệt';return JOBS[job.status]||'Chưa xác định'}
function definition(label,value){return el('div',{},el('dt',{text:label}),el('dd',{text:value}))}
function continuityDefinitions(video){
 const value=video.continuity,stamp=value=>typeof value==='string'&&/(?:Z|[+-]\d{2}:\d{2})$/.test(value)&&Number.isFinite(Date.parse(value));
 if(value?.eligible!==true||value.basis!=='reviewed_hash_unchanged'||!stamp(value.reviewed_at)||!stamp(value.validated_at)||Date.parse(value.validated_at)<Date.parse(value.reviewed_at))return [];
 return [definition('Lần duyệt quy trình',dateTime(value.reviewed_at,'America/Los_Angeles')),definition('Đối chiếu hash bằng máy',dateTime(value.validated_at,'America/Los_Angeles')),definition('Cơ sở đối chiếu','Hash giữ nguyên từ bản đã duyệt')];
}
function blockers(values){const items=list(values).filter(item=>typeof item==='string');return items.length?el('ul',{class:'idea-library-blockers'},items.map(value=>el('li',{text:value}))):null}
function fileSize(bytes){return Number.isFinite(bytes)&&bytes>=0?bytes>=1048576?(bytes/1048576).toFixed(1)+' MB':Math.ceil(bytes/1024)+' KB':'Chưa rõ dung lượng'}
const LIBRARY_ZONE='America/Los_Angeles';
function libraryDate(value){
 if(arguments.length&&(!value||!Number.isFinite(Date.parse(value))))return '';
 try{const parts=Object.fromEntries(new Intl.DateTimeFormat('en-CA',{timeZone:LIBRARY_ZONE,year:'numeric',month:'2-digit',day:'2-digit'}).formatToParts(arguments.length?new Date(value):new Date()).filter(part=>part.type!=='literal').map(part=>[part.type,part.value]));return `${parts.year.padStart(4,'0')}-${parts.month}-${parts.day}`}catch{return ''}
}
function periodLabel(value){const parts=value.split('-');return parts.length===3?`${parts[2]}/${parts[1]}/${parts[0]}`:parts.length===2?`${parts[1]}/${parts[0]}`:value}
function validPeriod(mode,value){
 if(mode==='all')return true;
 const pattern=['today','day'].includes(mode)?/^\d{4}-\d{2}-\d{2}$/:mode==='month'?/^\d{4}-\d{2}$/:/^\d{4}$/;
 if(!pattern.test(value))return false;const [year,month=1,day=1]=value.split('-').map(Number);
 return year>=1000&&year<=9999&&month>=1&&month<=12&&day>=1&&day<=new Date(Date.UTC(year,month,0)).getUTCDate();
}
const PIPELINE_STATES={pending:'Chờ',running:'Đang làm',passed:'Xong',needs_review:'Cần xem',manual_review_pending:'Bạn tự duyệt'};
function rollingProgress(run,total){
 const value=run?.pipeline,keys=(item,expected)=>item&&typeof item==='object'&&!Array.isArray(item)&&Object.keys(item).sort().join(',')===expected.sort().join(','),state=value=>typeof value==='string'&&Object.hasOwn(PIPELINE_STATES,value);
 if(!keys(value,['mode','variant_count','product','items'])||value.mode!=='rolling_v1'||!Number.isInteger(total)||total<1||total>14||value.variant_count!==total||(!state(value.product)||value.product==='manual_review_pending')||!Array.isArray(value.items)||value.items.length!==total)return null;
 if(value.items.some((row,index)=>!keys(row,['ordinal','image','video','qa'])||row.ordinal!==index+1||!state(row.image)||row.image==='manual_review_pending'||!state(row.video)||row.video==='manual_review_pending'||!state(row.qa)))return null;
 return value;
}
function rollingPanel(run,total){
 const value=rollingProgress(run,total);if(!value)return null;const manual=run?.review_policy==='manual_review_v1'&&run?.ai_qa===false||value.items.some(row=>row.qa==='manual_review_pending'),qaLabel=manual?'Bạn tự duyệt':'QA';
 const stage=(label,state)=>el('span',{class:'idea-rolling-state idea-rolling-'+state,text:PIPELINE_STATES[state],'aria-label':label+': '+PIPELINE_STATES[state]});
 const queues=[['image','Ảnh'],['video','Video'],['qa',qaLabel]].map(([key,label])=>{
  const counts=Object.fromEntries(Object.keys(PIPELINE_STATES).map(state=>[state,value.items.filter(row=>row[key]===state).length]));
  return el('div',{},el('strong',{text:label}),el('small',{text:counts.passed+' xong · '+counts.running+' đang làm · '+counts.pending+' chờ'+(counts.needs_review?' · '+counts.needs_review+' cần xem':'')+(counts.manual_review_pending?' · '+counts.manual_review_pending+' bạn tự duyệt':'')}));
 });
 return el('section',{class:'idea-rolling','aria-label':'Tiến độ từng video'},
  el('div',{class:'idea-rolling-heading'},el('strong',{text:'Tiến độ từng video'}),el('small',{text:'Worker báo cáo'})),
  el('div',{class:'idea-rolling-product'},el('span',{text:'Sản phẩm dùng chung'}),stage('Sản phẩm dùng chung',value.product)),
  el('div',{class:'idea-rolling-queues','aria-label':'Hàng đợi tạo nội dung'},queues),
  el('div',{class:'idea-rolling-grid',role:'table','aria-label':'Các bước theo video'},
   el('div',{class:'idea-rolling-row idea-rolling-labels',role:'row'},['Số','Ảnh','Dựng',qaLabel].map(label=>el('span',{role:'columnheader',text:label}))),
   value.items.map(row=>el('div',{class:'idea-rolling-row',role:'row','aria-label':'Video '+row.ordinal},el('strong',{role:'rowheader',text:String(row.ordinal)}),...['image','video','qa'].map((key,index)=>el('span',{role:'cell'},stage(['Ảnh','Dựng',qaLabel][index],row[key])))))),
  el('p',{class:'idea-rolling-gate',text:manual?'Video chờ bạn tự xem và duyệt qua Telegram trước khi đăng. Kiểm tra tệp và âm thanh không thay cho duyệt hình ảnh.':'Chỉ bàn giao khi đủ '+total+' video qua QA và bộ nhận xác nhận.'}));
}
function orchestrationPanel(value){
 const manual=value?.review_policy==='manual_review_v1';
 const stages=[
  {id:'product',provider:'codex',skills:['batch-remove-background'],tools:['run_bound_uploader'],label:'Sản phẩm',detail:'Upload đúng mẫu và xác minh link áo một lần trước khi tạo ảnh.'},
  {id:'image',provider:'codex',skills:['imagegen','realistic-shirt-photos-9x16','realistic-shirt-photos-closeup-9x16','ao-cao-cap'],tools:['native_imagegen'],label:'Ảnh riêng',detail:'Codex tạo ảnh · imagegen, ảnh áo thường / cận cảnh, ao-cao-cap.'},
  {id:'video',provider:'local',skills:[],tools:['ffmpeg','ffprobe','camera_qa','approved_pcm_check'],label:'Dựng video',detail:'FFmpeg / ffprobe · zoom 8 giây, kiểm tra chuyển động và PCM đã duyệt.'},
  {id:'qa',provider:'codex',skills:['us-video-review'],tools:['local_media_review'],label:'QA từng video',detail:'Codex rà video theo us-video-review.'},
  {id:'handoff',provider:'local',skills:[],tools:['offline_bridge','trusted_consumer'],label:'Bàn giao đủ bộ',detail:'Bridge và bộ nhận kiểm chứng đủ video trước khi đưa vào nguồn lịch.'}
 ];
 if(manual){
  stages[2]={...stages[2],tools:['ffmpeg','ffprobe','approved_pcm_check'],detail:'FFmpeg / ffprobe · video 8 giây, kiểm tra tệp và PCM đã duyệt.'};
  stages[3]={id:'qa',provider:'human',skills:[],tools:['human_video_review'],label:'Bạn tự duyệt',detail:'Bạn xem video và duyệt trên Telegram. Không chạy AI QA.'};
  stages[4]={...stages[4],label:'Chuyển từng video',detail:'Video dựng xong được bộ nhận đối chiếu và chuyển Telegram, không đợi đủ bộ.'};
 }
 if(value?.mode!=='rolling_v1'||typeof value.ready!=='boolean'||value.image_workers!==1||value.render_workers!==1||value.qa_workers!==(manual?0:1)||value.queue_capacity!==2||value.max_model_workers!==(manual?1:2)||value.product_scope!=='once_per_idea_before_images'||value.handoff_policy!==(manual?'per_variant_manual_review_pending':'complete_allocated_set')||!Array.isArray(value.stages)||value.stages.length!==stages.length)return null;
 if(stages.some((expected,index)=>{const actual=value.stages[index];return actual?.id!==expected.id||actual.provider!==expected.provider||JSON.stringify(actual.skills)!==JSON.stringify(expected.skills)||JSON.stringify(actual.tools)!==JSON.stringify(expected.tools)}))return null;
 return el('section',{class:'panel idea-orchestration','aria-label':'Điều phối từng video'},
  el('div',{class:'panel-heading'},el('h2',{text:'Điều phối từng video'}),badge(value.ready?'Đã cấu hình':'Chưa sẵn sàng',value.ready?'neutral':'warning')),
  el('div',{class:'panel-body'},el('p',{text:manual?'Ảnh xong đi tiếp sang dựng; từng video chuyển Telegram để bạn tự xem và duyệt trước khi đăng. Đây là cấu hình, chưa xác nhận công cụ đang chạy.':'Ảnh xong đi tiếp sang dựng và QA; ảnh sau có thể tiếp tục tạo. Đây là cấu hình, chưa xác nhận công cụ đang chạy.'}),
   el('ol',{class:'idea-orchestration-map'},stages.map(stage=>el('li',{},el('strong',{text:stage.label}),el('span',{text:stage.detail})))),
   el('p',{class:'idea-library-muted',text:manual?'Tối đa 1 worker mô hình · 1 lượt dựng cục bộ · mỗi hàng chờ tối đa 2. Không đợi đủ bộ; chưa duyệt thì chưa đăng.':'Tối đa 2 worker mô hình · 1 lượt dựng cục bộ · mỗi hàng chờ tối đa 2. Bàn giao cuối vẫn chờ đủ bộ.'})));
}

export function createIdeaPipeline({onSchedule}={}){
 const root=el('div',{class:'idea-library'});
 let mounted=false,data=null,assets=[],jobs=[],assetsLoaded=false,assetsError='',jobsError='',section='ideas',search='',filter='all',sort='warehouse',loading=false,scanning=false,refreshingViews=false,reading=false,sequence=0,poll=null,error='',scanError='',notice='',contentHost=null,lastRenderedFingerprint='';
 const reads=new Set();
 const attempts=new Map();
 const cancellations=new Map();
 const expandedDetails=new Set();
 const paging={ideas:{page:1,pageSize:12},videos:{page:1,pageSize:12},ranking:{page:1,pageSize:10}};
 let rankingOpen=false,assetsPage=null,dataQuery='',searchTimer=null,calendarControls=null;
 const calendars={ideas:{mode:'today',value:libraryDate()},videos:{mode:'today',value:libraryDate()}};
 let uploads={kind:'idea',items:[],busy:false,reviewed:false,reviewRequested:false};
 let uploaderBusy='',uploaderError='',uploaderNotice='';
 const uploaderChecks=new Map();
 const producerConnections=createProducerConnections(),ideaAutomation=createIdeaAutomation(),audioMixes=createAudioMixes(),projectAssets=createProjectAssets(),telegramApproval=createTelegramApproval();
 function cancelReads(){for(const controller of reads)controller.abort();reads.clear()}
 async function read(call){const controller=new AbortController();reads.add(controller);try{return await call(controller.signal)}finally{reads.delete(controller)}}
 function valid(value){return value&&typeof value==='object'&&value.pipeline&&typeof value.pipeline==='object'}
 function adopt(value){if(!valid(value))throw new Error('Invalid idea pipeline response');data={...data,...value}}
 function currentCalendar(){return calendars[section]}
 function validCalendar(){const value=currentCalendar();return !value||validPeriod(value.mode,value.value)}
 function calendarQuery(){const value=currentCalendar();return value?{calendar:value.mode==='all'?'':value.value,timezone:LIBRARY_ZONE}:{}}
 function syncToday(){let changed=false;const today=libraryDate();for(const key of ['ideas','videos'])if(calendars[key].mode==='today'&&calendars[key].value!==today){calendars[key].value=today;paging[key].page=1;changed=true}return changed}
 function queryKey(){return JSON.stringify({section,search,filter,sort,paging,rankingOpen,calendars})}
 function pipelineQuery(){const query={summary:true};if(supportsApiFeature('idea_views'))Object.assign(query,{view:section,...(section==='connections'?{include:'automation,telegram,audio_mixes,producers'}:{})});if(section==='ideas'){const state=paging.ideas;Object.assign(query,{limit:state.pageSize,offset:(state.page-1)*state.pageSize,search,status:filter,sort,...calendarQuery()});if(rankingOpen){const rank=paging.ranking;Object.assign(query,{ranking_limit:rank.pageSize,ranking_offset:(rank.page-1)*rank.pageSize})}}return query}
 function assetQuery(){const state=paging.videos;return {limit:state.pageSize,offset:(state.page-1)*state.pageSize,search,status:filter,...calendarQuery()}}
 function windowChanged({reset=false,debounce=false,forceRead=false}={}){
  if(reset)paging[section].page=1;pauseVideos();clearTimeout(searchTimer);
  if(forceRead||!validCalendar()||data?.pipeline?.paged||section==='videos'&&assetsPage){sequence++;cancelReads();reading=false;renderCards();if(validCalendar())searchTimer=setTimeout(()=>void load(),debounce?250:0)}else renderCards();
 }
 function totalIdeas(){return data?.pipeline?.summary?.idea_count??data?.pipeline?.idea_count??ideas().length}
 function disclosure(key,title,content){
  const details=el('details',{class:'idea-library-details',open:expandedDetails.has(key)}),body=el('div',{class:'idea-library-detail-body'});
  const populate=()=>{if(details.open){expandedDetails.add(key);if(expandedDetails.size>100)expandedDetails.delete(expandedDetails.values().next().value);body.replaceChildren(...content())}else{expandedDetails.delete(key);body.replaceChildren()}};
  details.append(el('summary',{text:title}),body);if(details.open)populate();details.addEventListener('toggle',populate);return details;
 }
 function fingerprint(){
  const p=data?.pipeline||{},settings=p.settings||{},policy=data?.policy||{},upload=data?.upload||{},video=data?.video_agent||{};
  const catalog=list(p.ideas||p.items).map(idea=>({id:idea.id,filename:idea.filename,status:idea.status,allocated_variant_count:idea.allocated_variant_count,campaign_state:idea.campaign_state,campaign_url:idea.campaign?.campaign_url,artwork_sha256:idea.artwork_sha256,variants:list(idea.variants).map(row=>({id:row.id,status:row.status,verification:row.verification,asset_id:row.asset_id,video_sha256:row.video_sha256}))}));
  const currentPerformance=section==='ideas'?ideas().map(idea=>({id:idea.id,...performanceOf(idea)})):undefined;
  return JSON.stringify({section,search,filter,sort,paging,calendars,dataQuery,assetsPage,summary:data?.pipeline?.summary,ideaCount:data?.pipeline?.idea_count,catalog,currentPerformance,allocation:list(data?.automation?.active_batch?.items).map(row=>({idea_id:row.idea_id,variant_count:row.variant_count})),execution:data?.execution||p.execution,runs:runItems().map(row=>({id:row.id,idea_id:row.idea_id,variant_count:row.variant_count,status:row.status,message:row.message,can_cancel:row.can_cancel,pipeline:rollingProgress(row,row.variant_count)})),assets:section==='videos'?assets.map(row=>({id:row.id,name:row.name,size:row.size,created_at:row.created_at,preview_url:row.preview_url})):undefined,jobs:section==='videos'?jobs.map(row=>({id:row.id,asset_id:row.asset_id,page_id:row.page_id,page_name:row.page_name,status:row.status,step:row.step,publication_approval:row.publication_approval,scheduled_at:row.scheduled_at,publish_mode:row.publish_mode,approve_by:row.approve_by,timezone:row.timezone,caption:row.caption})):undefined,connections:section==='connections'?{telegram:data?.telegram,project_assets:data?.project_assets,audio_mixes:data?.audio_mixes,automation:data?.automation,producers:data?.producers,directory:p.directory,source:settings.campaign_source,black_source:settings.black_campaign_source,source_requested:policy.source_requested,black_requested:policy.black_source_requested,upload:{provider:upload.provider,endpoint:upload.endpoint,providers:upload.providers,available:upload.available,ready:upload.ready,tool_root:upload.tool_root,active_domain:upload.active_domain,authenticated:upload.authenticated,blockers:upload.blockers},video:{continuity:video.continuity,status:video.status,ready:video.ready,title:video.title,skill:video.skill,skill_path:video.skill_path,message:video.message,blockers:video.blockers}}:undefined,readiness:{upload_ready:upload.ready,upload_blockers:upload.blockers,video_status:video.status,video_ready:video.ready},ranking:section==='ideas'?list((data?.ranking||p.ranking)?.cohorts).map(cohort=>({metric:cohort.metric,items:list(cohort.items).map(row=>({idea_id:row.idea_id,filename:row.filename,sample_count:row.sample_count,median_views_24h:row.median_views_24h,median_page_lift:row.median_page_lift}))})):undefined,attempts:[...attempts].map(([id,row])=>({id,busy:row.busy,uncertain:row.uncertain,readAfterUnknown:row.readAfterUnknown,error:row.error})),cancellations:[...cancellations].map(([id,row])=>({id,busy:row.busy,error:row.error})),uploads:{kind:uploads.kind,busy:uploads.busy,reviewed:uploads.reviewed,items:uploads.items.map(row=>({name:row.name,status:row.status,message:row.message}))},assetsLoaded,assetsError,jobsError,error,scanError,notice,scanning:!!scanning});
 }
 function pauseVideos(){for(const node of root.querySelectorAll('video,audio'))node.pause()}
 function releaseVideos(){for(const node of root.querySelectorAll('video')){node.pause();node.removeAttribute('src');node.load()}}
 function refreshAfterWrite(options){
  if(!mounted)return;
  sequence++;cancelReads();reading=false;
  return load(options);
 }
 async function load({automatic=false}={}){
  if(!mounted)return;
  if(syncToday()){sequence++;cancelReads();reading=false;renderCalendarControls();renderCards()}
  if(!validCalendar()){renderCards();return}
  if(reading||scanning)return;
  clearTimeout(searchTimer);const stamp=++sequence,key=queryKey();reading=stamp;loading=!data;error='';if(loading)render();
  const tasks=section==='videos'?[['assets',()=>read(signal=>readPageWindow(api.tokenReelAssets,assetQuery(),signal))]]:
   section==='connections'&&automatic&&supportsApiFeature('idea_views')?[['automation',()=>read(api.ideaAutomation)],['telegram',()=>read(api.telegramApproval)]]:
   [['pipeline',()=>read(signal=>api.ideaPipeline(pipelineQuery(),signal))]];
  const results=await Promise.allSettled(tasks.map(([,call])=>call()));
  if(!mounted||stamp!==sequence)return;
  if(key!==queryKey()){reading=false;void load({automatic});return}
  let pipelineAccepted=section==='videos'||section==='connections'&&automatic,assetsAccepted=section!=='videos';
  results.forEach((result,index)=>{const key=tasks[index][0];if(result.status==='rejected'){if(result.reason.name==='AbortError')return;const message=result.reason.status===404&&key==='pipeline'?'Kho nội dung chưa sẵn sàng trên server. Mở lại server để nạp bản mới.':safeMessage(result.reason);if(key==='pipeline')error=message;else if(key==='assets')assetsError=message;else error=message;return}const value=result.value;if(key==='automation'||key==='telegram'){data={...data,[key]:value}}else if(key==='pipeline'){try{adopt(value);pipelineAccepted=true;const window=value.pipeline.pagination;if(window){paging.ideas.pageSize=window.limit;paging.ideas.page=Math.floor(window.offset/window.limit)+1}if(Number.isFinite(value.ranking?.total)){paging.ranking.pageSize=value.ranking.limit;paging.ranking.page=Math.floor(value.ranking.offset/value.ranking.limit)+1}}catch{error='Chưa đọc được sổ nội dung hợp lệ.'}}else if(key==='assets'){assets=list(value.assets);assetsPage=Number.isFinite(value.total)?{total:value.total,limit:value.limit,offset:value.offset,summary:value.summary}:null;if(assetsPage){paging.videos.pageSize=value.limit;paging.videos.page=Math.floor(value.offset/value.limit)+1}assetsLoaded=true;assetsAccepted=true;assetsError='';jobsError='';jobs=assets.flatMap(asset=>list(asset.linked_jobs))}});
  for(const [ideaId,attempt] of attempts){const known=runItems().find(row=>row.request_id===attempt.requestId)||runItems().find(row=>String(row.idea_id)===ideaId);if(known){attempt.uncertain=false;attempt.error=''}else if(attempt.uncertain)attempt.readAfterUnknown=true}
  if(uploads.reviewRequested&&!error&&(!assetsError||uploads.kind==='idea')){uploads.reviewed=true;uploads.reviewRequested=false}
  reading=false;loading=false;if(pipelineAccepted&&assetsAccepted)dataQuery=queryKey();
  const inspectingVideo=[...root.querySelectorAll('video,audio')].some(node=>!node.ended&&(!node.paused||node.currentTime>0));
  const editingUploader=section==='connections'&&[...root.querySelectorAll('.idea-uploader-login input,.telegram-settings input')].some(node=>node===document.activeElement||node.type==='password'&&node.value);
  // A paused frame is still a user's inspection position. Defer automatic
  // metadata changes until playback ends or the user explicitly refreshes.
  if(!automatic||!inspectingVideo&&!editingUploader&&!uploaderBusy&&fingerprint()!==lastRenderedFingerprint)render();
 }
 async function scan(){
  if(!mounted||scanning||!data)return;
  const stamp=++sequence;scanning=stamp;cancelReads();reading=false;error='';scanError='';notice='';render();
  try{const value=await api.scanIdeaPipeline({summary:true});if(!mounted||stamp!==sequence)return;if(valid(value)&&!value.pipeline.paged)adopt(value);notice='Đã yêu cầu ghi nhận idea. Chưa upload hoặc tạo video; bấm Chạy idea khi bộ chạy sẵn sàng.'}
  catch(reason){if(mounted&&stamp===sequence)scanError=reason.status===0?'Chưa xác định đầy đủ kết quả quét. Đọc lại trạng thái trước khi thao tác tiếp.':safeMessage(reason)}
  finally{if(scanning===stamp){scanning=false;if(mounted){render();void refreshAfterWrite()}}}
 }
 function changeSection(value){if(section===value)return;pauseVideos();sequence++;cancelReads();reading=false;clearTimeout(searchTimer);section=value;search='';filter='all';render();void load()}
 function panelHeading(title,description,action){return el('div',{class:'panel-heading'},el('div',{},el('h2',{text:title}),el('p',{text:description})),action)}
 function ideas(){return list(data?.pipeline?.ideas||data?.pipeline?.items)}
 function runItems(){return list(data?.runs?.items||data?.pipeline?.runs)}
 function knownRun(idea){const values=runItems().filter(row=>String(row.idea_id)===String(idea.id));return values.find(row=>['queued','preparing','running','needs_review','completed'].includes(row.status))||values[0]}
 function variantCount(idea){
  const run=knownRun(idea),frozen=list(data?.automation?.active_batch?.items).find(row=>String(row.idea_id)===String(idea.id));
  for(const value of [run?.variant_count,frozen?.variant_count,idea.allocated_variant_count,list(idea.variants).length,data?.policy?.legacy_default_variant_count])if(Number.isInteger(value)&&value>=1&&value<=14)return value;
  return 7;
 }
 function variantReady(variant){if(variant.verification==='manual_visual_review_pending')return false;return variant.status==='metadata_registered'&&variant.verification==='worker_qa_approved'||['ready','verified','published','completed'].includes(variant.status)&&variant.verification!=='metadata_only'}
 function campaignOf(idea){return idea.campaign_state==='verified'?productUrl(idea.campaign?.campaign_url):null}
 function performanceOf(idea){
  if(idea.performance&&typeof idea.performance==='object')return idea.performance;
  const ids=new Set(list(idea.variants).map(row=>String(row.id))),latest=new Map();
  for(const row of list(data?.performance?.items)){
   if(String(row.idea_id)!==String(idea.id)||row.metric!=='views'||!ids.has(String(row.variant_id)))continue;
   const id=String(row.variant_id),previous=latest.get(id),time=Date.parse(row.observed_at),previousTime=Date.parse(previous?.observed_at);
   if(!previous||Number.isFinite(time)&&(!Number.isFinite(previousTime)||time>=previousTime))latest.set(id,row);
  }
  const known=[...latest.values()].filter(row=>row.metric_verified===true&&row.availability==='available'&&Number.isSafeInteger(row.value)&&row.value>=0),sum=known.reduce((total,row)=>total+row.value,0);
  return {views:known.length&&Number.isSafeInteger(sum)?sum:null,known:known.length,total:variantCount(idea)};
 }
 function updateViewsButton(){const node=root.querySelector('[data-action="refresh-views"]');if(node){node.disabled=refreshingViews;const label=node.querySelector('span');if(label)label.textContent=refreshingViews?'Đang đọc lượt xem…':'Cập nhật lượt xem'}}
 async function refreshViews(){
  if(refreshingViews)return;refreshingViews=true;updateViewsButton();
  try{await api.refreshIdeaViews();toast('Đã đọc lại lượt xem khả dụng. Số liệu thiếu vẫn được giữ là chưa có dữ liệu.')}
  catch(reason){toast(safeMessage(reason),'error')}
  finally{refreshingViews=false;if(mounted){updateViewsButton();void refreshAfterWrite({automatic:true})}}
 }
 function runBlocker(idea){
  const execution=data?.execution||data?.pipeline?.execution||{};
  const run=knownRun(idea),attempt=attempts.get(String(idea.id));
  if(attempt?.busy)return 'Đang gửi yêu cầu chạy idea; không gửi lặp.';
  if(run&&['queued','preparing','running'].includes(run.status))return 'Idea đang có lượt tạo nội dung; tiến độ tự cập nhật.';
  if(run?.status==='completed')return 'Idea đã hoàn tất '+variantCount(idea)+' video; không tự tạo lại.';
  if(run?.status==='needs_review')return text(run.message)||'Lượt tạo trước cần đối chiếu trước khi chạy tiếp.';
  if(attempt?.uncertain&&!attempt.readAfterUnknown)return 'Chưa rõ kết quả. Đọc lại trạng thái trước khi thử lại.';
  if(execution.supported===true)return '';
  const messages=list(execution.blockers).filter(value=>typeof value==='string');
  if(messages.length)return messages[0];
  if(data?.upload?.ready!==true)return list(data?.upload?.blockers).find(value=>typeof value==='string')||'Tool upload chưa xác minh đúng shop và campaign mẫu.';
  if(data?.video_agent?.status!=='ready'&&data?.video_agent?.ready!==true)return 'Agent tạo video chưa xác nhận sẵn sàng bàn giao.';
  if(!campaignOf(idea))return 'Bộ chạy cần hoàn tất upload và xác minh link áo của idea.';
  return 'Bộ chạy thủ công Chạy idea chưa được kết nối; chưa tạo nội dung.';
 }
 async function startIdea(idea){
  if(!mounted||runBlocker(idea))return;
  const id=String(idea.id),previous=attempts.get(id),attempt={requestId:previous?.requestId||crypto.randomUUID(),busy:true,uncertain:false,readAfterUnknown:false,error:''};attempts.set(id,attempt);renderCards();
  try{const result=await api.runIdea(id,{request_id:attempt.requestId});if(!result?.run)throw new Error('Missing run result');const rows=runItems();data.runs={items:[result.run,...rows.filter(row=>String(row.id)!==String(result.run.id))]};attempt.error=''}
  catch(reason){attempt.uncertain=reason.status===0||!reason.status;attempt.error=attempt.uncertain?'Chưa rõ kết quả chạy idea. Đọc lại trạng thái; lần thử lại giữ cùng mã yêu cầu.':safeMessage(reason)}
  finally{attempt.busy=false;if(mounted){renderCards();void refreshAfterWrite()}}
 }
 async function cancelRun(run){
  const id=String(run.id),previous=cancellations.get(id);if(!mounted||run.can_cancel!==true||previous?.busy)return;
  const attempt={busy:true,error:''};cancellations.set(id,attempt);renderCards();
  try{const result=await api.cancelIdeaRun(id);if(!result?.run)throw new Error('Missing cancellation result');data.runs={items:[result.run,...runItems().filter(row=>String(row.id)!==id)]}}
  catch(reason){attempt.error=!reason.status?'Chưa rõ kết quả dừng. Đọc lại trạng thái trước khi thao tác tiếp.':safeMessage(reason)}
  finally{attempt.busy=false;if(mounted){renderCards();void refreshAfterWrite()}}
 }
 function beginUpload(kind,files){
  if(uploads.busy)return;const values=[...files];if(!values.length)return;
  if(values.length>20){notice='Chọn tối đa 20 tệp mỗi lượt để tải lần lượt.';render();return}
  uploads={kind,items:values.map(file=>({file,name:file.name,status:'waiting',message:''})),busy:false,reviewed:false,reviewRequested:false};notice='';scanError='';void processUploads();
 }
 async function processUploads(){
  if(uploads.busy)return;const batch=uploads;batch.busy=true;if(mounted)render();
  for(const item of batch.items){
   if(item.status!=='waiting')continue;
   const extension='.'+item.name.split('.').at(-1).toLowerCase(),isIdea=batch.kind==='idea',extensions=isIdea?['.png','.jpg','.jpeg','.webp']:['.mp4','.mov','.m4v'],limit=isIdea?25*1048576:1073741824;
   if(!extensions.includes(extension)||item.file.size<=0||item.file.size>limit){item.status='invalid';item.message=isIdea?'Chọn PNG, JPG hoặc WEBP từ 1 byte đến 25 MB.':'Chọn MP4, MOV hoặc M4V từ 1 byte đến 1 GB.';continue}
   item.status='sending';if(mounted)render();
   try{const result=isIdea?await api.uploadIdea(item.file):await api.tokenReelUpload(item.file);if(isIdea?!result?.idea:!result?.asset)throw new Error('Missing upload result');item.status=result.duplicate===true?'duplicate':'stored';item.message=result.duplicate===true?'Đã có trong kho':'Đã lưu vào kho';item.file=null}
   catch(reason){item.status=!reason.status?'unknown':'failed';item.message=item.status==='unknown'?'Chưa rõ kết quả. Đọc lại kho trước khi thử lại.':safeMessage(reason);if(item.status==='unknown'){for(const next of batch.items)if(next.status==='waiting'){next.status='paused';next.message='Chưa gửi; chờ đối chiếu tệp trước.'}break}}
   if(mounted)render();
  }
  batch.busy=false;if(mounted){render();void refreshAfterWrite()}
 }
 function reviewUploads(){uploads.reviewRequested=true;void refreshAfterWrite()}
 function retryUploads(){if(uploads.busy||uploads.items.some(item=>item.status==='unknown')&&!uploads.reviewed)return;for(const item of uploads.items)if(['unknown','paused','failed'].includes(item.status))item.status='waiting';uploads.reviewed=false;uploads.reviewRequested=false;void processUploads()}
 function uploadPanel(){
  const errors=uploads.items.some(item=>['invalid','failed','unknown','paused'].includes(item.status)),labels={waiting:'Chờ tải',sending:'Đang tải',stored:'Đã lưu',duplicate:'Đã có',invalid:'Tệp chưa hợp lệ',failed:'Chưa tải xong',unknown:'Cần đối chiếu',paused:'Chưa gửi'},panel=el('section',{class:'idea-library-uploads','aria-label':'Tiến độ tải vào kho'},el('div',{class:'idea-library-upload-heading'},el('strong',{text:uploads.busy?'Đang tải '+(uploads.kind==='idea'?'idea':'video')+' vào kho…':'Kết quả tải vào kho'}),badge(uploads.busy?'Đang tải':errors?'Cần kiểm tra':'Đã lưu',uploads.busy?'neutral':errors?'warning':'success')));
  panel.append(el('ul',{},uploads.items.map(item=>el('li',{},el('strong',{text:item.name}),el('span',{text:labels[item.status]||'Chưa xác định'}),item.message?el('small',{text:item.message}):null))),el('p',{text:uploads.kind==='idea'?'Tải ảnh chỉ thêm idea vào kho. Chưa upload sản phẩm hoặc tạo video.':'Tải video chỉ lưu tệp vào manager. Chưa tạo lịch hoặc đăng lên Page.'}));
  if(errors&&!uploads.busy)panel.append(el('div',{class:'idea-library-toolbar-actions'},button('Đọc lại kho sau tải lên',reviewUploads,{small:true,iconName:'refresh'}),button('Thử lại tệp chờ',retryUploads,{small:true,disabled:uploads.items.some(item=>item.status==='unknown')&&!uploads.reviewed||!uploads.items.some(item=>['unknown','paused','failed'].includes(item.status))})));
  return panel;
 }
 function render(){
  if(!mounted)return;const active=root.contains(document.activeElement)?document.activeElement:null,focusLabel=active?.getAttribute('aria-label'),selection=active instanceof HTMLInputElement?[active.selectionStart,active.selectionEnd]:null;releaseVideos();root.replaceChildren();lastRenderedFingerprint=fingerprint();
  if(loading&&!data){root.append(skeleton(5));return}
  if(!data){root.append(el('div',{class:'idea-library-error',role:'alert',text:error||'Chưa đọc được kho nội dung.'}),button('Đọc lại kho nội dung',()=>void load(),{iconName:'refresh',disabled:!!reading}));return}
  const ideaCount=totalIdeas();
  const tabs=el('div',{class:'idea-library-tabs',role:'tablist','aria-label':'Thư viện nội dung'});
  for(const [key,label] of [['ideas','Idea'],['videos','Video'],['connections','Kết nối']])tabs.append(el('button',{type:'button',role:'tab',id:'library-tab-'+key,'aria-selected':section===key?'true':'false','aria-controls':'library-content',tabindex:section===key?'0':'-1',onClick:()=>changeSection(key),onKeydown:event=>{const keys=['ideas','videos','connections'],offset=event.key==='ArrowRight'?1:event.key==='ArrowLeft'?-1:0;if(!offset)return;event.preventDefault();const next=keys[(keys.indexOf(key)+offset+keys.length)%keys.length];changeSection(next);root.querySelector('#library-tab-'+next)?.focus()}},el('span',{text:label}),key==='ideas'?el('small',{text:ideaCount}):key==='videos'&&assetsLoaded?el('small',{text:assetsPage?.summary?.total??assetsPage?.total??assets.length}):null));
  root.append(el('div',{class:'idea-library-header'},tabs,button('Lịch đăng Page',()=>onSchedule?.(),{iconName:'calendar',small:true})));
  if(uploads.items.length)root.append(uploadPanel());
  if(error)root.append(el('p',{class:'idea-library-error',role:'alert',text:error}));
  if(scanError)root.append(el('p',{class:'idea-library-error',role:'alert',text:scanError}));
  if(notice)root.append(el('p',{class:'idea-library-notice',role:'status',text:notice}));
  contentHost=el('section',{id:'library-content',role:'tabpanel','aria-labelledby':'library-tab-'+section});root.append(contentHost);
  if(section==='connections'){renderConnections();return}
  renderLibrary();
  if(focusLabel){const restored=[...root.querySelectorAll('[aria-label]')].find(node=>node.getAttribute('aria-label')===focusLabel);restored?.focus({preventScroll:true});if(selection&&restored instanceof HTMLInputElement&&selection[0]!==null)restored.setSelectionRange(...selection)}
 }
 function renderCalendarControls(){
  if(!calendarControls||!currentCalendar())return;
  const value=currentCalendar(),kind=section==='ideas'?'idea':'video',period=el('select',{'aria-label':'Xem '+kind+' theo ngày tháng năm',onChange:event=>{value.mode=event.target.value;const today=libraryDate();value.value=['today','day'].includes(value.mode)?today:value.mode==='month'?today.slice(0,7):value.mode==='year'?today.slice(0,4):'';renderCalendarControls();windowChanged({reset:true,forceRead:true})}},[['today','Hôm nay'],['all','Tất cả ngày'],['day','Theo ngày'],['month','Theo tháng'],['year','Theo năm']].map(([value,text])=>el('option',{value,text})));period.value=value.mode;
  const nodes=[el('label',{class:'idea-library-calendar-period'},el('span',{text:'Ngày thêm vào kho'}),period)];
  if(!['today','all'].includes(value.mode)){
   const label=value.mode==='day'?'Ngày thêm '+kind+' (dd/MM/yyyy)':value.mode==='month'?'Tháng thêm '+kind+' (MM/yyyy)':'Năm thêm '+kind+' (yyyy)',preview=el('small',{class:'idea-library-calendar-preview','aria-live':'polite',text:validCalendar()?'Đang lọc: '+periodLabel(value.value):'Chưa chọn kỳ lọc hợp lệ'});
   const input=el('input',{type:value.mode==='year'?'number':value.mode==='month'?'month':'date',lang:'vi',value:value.value,...(value.mode==='year'?{min:1000,max:9999,step:1}:value.mode==='month'?{min:'1000-01',max:'9999-12'}:{min:'1000-01-01',max:'9999-12-31'}),'aria-label':label,onInput:event=>{value.value=event.target.value;preview.textContent=validCalendar()?'Đang lọc: '+periodLabel(value.value):'Chưa chọn kỳ lọc hợp lệ';const zone=calendarControls.querySelector('.idea-library-calendar-zone');if(zone)zone.textContent=(validCalendar()?'Đang lọc: '+periodLabel(value.value):'Chọn kỳ lọc hợp lệ')+' · '+LIBRARY_ZONE;windowChanged({reset:true,forceRead:true})}});
   nodes.push(el('label',{},el('span',{text:label}),input,preview));
  }
  nodes.push(el('p',{class:'idea-library-calendar-zone',text:(value.mode==='today'?'Hôm nay: '+periodLabel(value.value):value.mode==='all'?'Tất cả ngày':validCalendar()?'Đang lọc: '+periodLabel(value.value):'Chọn kỳ lọc hợp lệ')+' · '+LIBRARY_ZONE}));
  calendarControls.replaceChildren(...nodes);
 }
 function renderLibrary(){
  if(!contentHost)return;
  const toolbar=el('div',{class:'idea-library-toolbar'}),searchInput=el('input',{type:'search',value:search,placeholder:section==='ideas'?'Tìm thiết kế hoặc idea…':'Tìm video hoặc Page…','aria-label':section==='ideas'?'Tìm trong kho idea':'Tìm trong kho video',onInput:event=>{search=event.target.value;windowChanged({reset:true,debounce:true})}});
  const select=el('select',{'aria-label':section==='ideas'?'Lọc idea':'Lọc video',onChange:event=>{filter=event.target.value;windowChanged({reset:true})}},el('option',{value:'all',text:'Tất cả'}),...(section==='ideas'?[el('option',{value:'pending',text:'Chưa có link áo'}),el('option',{value:'verified',text:'Đã có link áo'}),el('option',{value:'review',text:'Cần kiểm tra'})]:[el('option',{value:'unused',text:'Chưa đặt lịch'}),el('option',{value:'scheduled',text:'Có lịch đăng'}),el('option',{value:'published',text:'Đã đăng'})]));select.value=filter;
  const isIdea=section==='ideas',fileInput=el('input',{type:'file',accept:isIdea?'.png,.jpg,.jpeg,.webp':'.mp4,.mov,.m4v',multiple:true,hidden:true,'aria-label':isIdea?'Chọn ảnh idea để thêm vào kho':'Chọn video để thêm vào kho',onChange:event=>{const files=[...event.target.files];event.target.value='';beginUpload(isIdea?'idea':'video',files)}});
  const sortSelect=isIdea?el('select',{'aria-label':'Sắp xếp idea',onChange:event=>{sort=event.target.value;windowChanged({reset:true})}},el('option',{value:'warehouse',text:'Thứ tự trong kho'}),el('option',{value:'views',text:'Lượt xem hiện tại'})):null;if(sortSelect)sortSelect.value=sort;
  toolbar.append(el('div',{class:'idea-library-search'},icon('search',16),searchInput),select,...(sortSelect?[sortSelect]:[]),el('div',{class:'idea-library-toolbar-actions'},button(isIdea?'Thêm idea':'Thêm video',()=>fileInput.click(),{style:'primary',iconName:'plus',disabled:uploads.busy}),isIdea?button('Đọc lại idea',()=>void load(),{iconName:'refresh',small:true}):button('Đọc lại video',()=>void load(),{iconName:'refresh',disabled:!!reading})),fileInput);
  const drop=el('div',{class:'idea-library-dropzone',onDragover:event=>{if([...event.dataTransfer?.types||[]].includes('Files')){event.preventDefault();event.dataTransfer.dropEffect=uploads.busy?'none':'copy'}},onDrop:event=>{event.preventDefault();if(!uploads.busy)beginUpload(isIdea?'idea':'video',event.dataTransfer.files)}},icon(isIdea?'posts':'calendar',19),el('p',{text:isIdea?'Kéo ảnh vào đây hoặc bấm Thêm idea · PNG, JPG, WEBP, tối đa 25 MB/tệp. Vẫn có thể chép ảnh vào C:\\Users\\Admin\\Via\\media\\ideas.':'Kéo video vào đây hoặc bấm Thêm video · MP4, MOV, M4V, tối đa 1 GB/tệp. Chỉ lưu vào kho, chưa đặt lịch.'}));
  const tools=disclosure('tools-'+section,isIdea?'Nhập từ thư mục & hướng dẫn':'Thêm tệp & hướng dẫn',()=>[drop,...(isIdea?[button(scanning?'Đang quét…':'Quét thư mục',()=>void scan(),{disabled:!!scanning,small:true,iconName:'refresh'})]:[]),el('p',{class:'idea-library-help',text:isIdea?'Tạo nội dung bằng Chạy idea hoặc chế độ tự động đã bật ở mục Kết nối. Caption chỉ chứa link áo; lịch chỉ đăng đúng giờ sau khi đủ các chốt duyệt.':'Video thật đã lưu trên manager. Tạo nội dung là thao tác riêng; lịch chỉ đăng khi đến giờ và đủ các chốt duyệt.'})]);tools.classList.add('idea-library-import-tools');
  calendarControls=el('div',{class:'idea-library-calendar-controls'});renderCalendarControls();
  contentHost.append(toolbar,calendarControls,tools,el('div',{class:'idea-library-cards-host'}));
  renderCards();
  if(section==='ideas')contentHost.append(rankingPanel(data.ranking||data.pipeline.ranking||{}));lastRenderedFingerprint=fingerprint();
 }
 function paged(values,key){const state=paging[key];state.page=Math.max(1,Math.min(state.page,Math.max(1,Math.ceil(values.length/state.pageSize))));return values.slice((state.page-1)*state.pageSize,state.page*state.pageSize)}
 function pager(key,total,redraw=renderCards){
  const state=paging[key],label={ideas:'Kho idea',videos:'Kho video',ranking:'Hiệu quả idea'}[key];
  return createPagination({label,total,page:state.page,pageSize:state.pageSize,pageSizes:key==='ranking'?[10,25,50,100]:[12,24,48],onPageChange:value=>{state.page=value;if(data?.pipeline?.paged||key==='videos'&&assetsPage)windowChanged();else redraw();lastRenderedFingerprint=fingerprint()},onPageSizeChange:value=>{state.pageSize=value;state.page=1;if(data?.pipeline?.paged||key==='videos'&&assetsPage)windowChanged();else redraw();lastRenderedFingerprint=fingerprint()}});
 }
 function renderCards(){
  const host=contentHost?.querySelector('.idea-library-cards-host');if(!host)return;releaseVideos();host.replaceChildren();lastRenderedFingerprint=fingerprint();if(!validCalendar()){host.append(el('p',{class:'idea-library-calendar-invalid',role:'status',text:'Chọn ngày, tháng hoặc năm hợp lệ để xem '+(section==='ideas'?'idea.':'video.')}));return}if(data?.pipeline?.paged&&dataQuery!==queryKey()){host.append(error||assetsError?el('p',{class:'idea-library-error',role:'alert',text:error||assetsError}):skeleton(3));return}
  const term=search.trim().toLocaleLowerCase('vi');
  if(section==='ideas'){
   const remote=!!data?.pipeline?.pagination,values=remote?ideas():ideas().filter(idea=>(currentCalendar().mode==='all'||libraryDate(idea.created_at).startsWith(currentCalendar().value))&&(!term||(text(idea.filename)+' '+text(idea.id)).toLocaleLowerCase('vi').includes(term))&&(filter==='all'||filter==='verified'&&!!campaignOf(idea)||filter==='pending'&&!campaignOf(idea)||filter==='review'&&['uncertain','needs_review','failed','error'].includes(idea.campaign_state||idea.status)));
   if(!values.length){paging.ideas.page=1;host.append(empty(totalIdeas()?'Không có idea phù hợp':'Kho idea đang trống',totalIdeas()?'Thử đổi từ khóa hoặc bộ lọc.':'Đưa PNG, JPG hoặc WEBP vào C:\\Users\\Admin\\Via\\media\\ideas rồi bấm Quét idea mới.'));lastRenderedFingerprint=fingerprint();return}
   if(!remote&&sort==='views')values.sort((a,b)=>{const av=performanceOf(a).views,bv=performanceOf(b).views;return av===null&&bv===null?0:av===null?1:bv===null?-1:bv-av});
   const grid=el('div',{class:'idea-library-grid'});for(const idea of remote?values:paged(values,'ideas'))grid.append(ideaCard(idea));host.append(grid,pager('ideas',remote?data.pipeline.pagination.total:values.length));lastRenderedFingerprint=fingerprint();return;
  }
  if(assetsError){host.append(el('p',{class:'idea-library-error',role:'alert',text:'Chưa tải được kho video. '+assetsError}));return}
  if(!assetsLoaded){host.append(skeleton(3));return}
  if(jobsError)host.append(el('p',{class:'idea-library-error',role:'alert',text:'Chưa đọc được trạng thái lịch. '+jobsError}));
  const values=assetsPage?assets:assets.filter(asset=>{const linked=jobs.filter(job=>String(job.asset_id)===String(asset.id)),haystack=(text(asset.name)+' '+linked.map(job=>job.page_name||job.page_id).join(' ')).toLocaleLowerCase('vi');return (currentCalendar().mode==='all'||libraryDate(asset.created_at).startsWith(currentCalendar().value))&&(!term||haystack.includes(term))&&(filter==='all'||filter==='unused'&&!linked.length||filter==='scheduled'&&linked.some(job=>!['cancelled','completed'].includes(job.status))||filter==='published'&&linked.some(job=>job.status==='completed'))});
  if(!values.length){paging.videos.page=1;const planned=data?.pipeline?.summary?.planned_video_count??ideas().reduce((total,idea)=>total+list(idea.variants).filter(variant=>!variant.asset_id).length,0);host.append(empty((assetsPage?.summary?.total??assets.length)?'Không có video phù hợp':'Chưa có video trong kho',(assetsPage?.summary?.total??assets.length)?'Thử đổi từ khóa hoặc bộ lọc.':planned?'Có '+planned+' biến thể đã lên phương án. Video chỉ hiện tại đây sau khi tệp thật được lưu vào manager.':'Video sẽ hiện sau khi tạo và lưu vào manager. Chưa có tệp video để xem trước.'));lastRenderedFingerprint=fingerprint();return}
  const grid=el('div',{class:'idea-library-grid idea-library-video-grid'});for(const asset of assetsPage?values:paged(values,'videos'))grid.append(videoCard(asset));host.append(grid,pager('videos',assetsPage?.total??values.length));lastRenderedFingerprint=fingerprint();
 }
 function ideaCard(idea){
  const variants=list(idea.variants),total=variantCount(idea),done=variants.filter(variantReady).length,url=campaignOf(idea),id=String(idea.id||''),blocker=runBlocker(idea),attempt=attempts.get(id),runState=knownRun(idea),preview=el('div',{class:'idea-library-art'},el('span',{class:'idea-library-art-fallback'},icon('posts',34),el('small',{text:'Thiết kế của idea'})));
  if(/^[a-zA-Z0-9_-]+$/.test(id))preview.append(el('img',{src:resourceUrl('/api/idea-pipeline/ideas/'+encodeURIComponent(id)+'/image'),alt:'Thiết kế '+(text(idea.filename)||'idea'),loading:'lazy',decoding:'async',referrerpolicy:'no-referrer',onLoad:()=>preview.classList.add('has-image'),onError:event=>event.currentTarget.remove()}));
  const title=el('div',{class:'idea-library-card-title'},el('h3',{text:text(idea.filename)||'Idea chưa có tên'}),stateBadge(idea.campaign_state==='verified'&&!url?'needs_review':idea.campaign_state||idea.status||'new'));
  const product=url?el('a',{class:'idea-library-product',href:url,target:'_blank',rel:'noopener noreferrer',text:'Mở link áo'}):null;
  const manual=variants.filter(variant=>variant.verification==='manual_visual_review_pending').length,progress=el('div',{class:'idea-library-progress'},el('strong',{text:done+' / '+total+' video'}),el('small',{text:manual?manual+' video · Bạn tự duyệt':runState?.review_policy==='manual_review_v1'&&runState?.ai_qa===false?'Bạn tự duyệt trên Telegram':'đã xác minh'}));
  const performance=performanceOf(idea),views=el('div',{class:'idea-library-current-views'},el('span',{text:'Lượt xem'}),el('strong',{text:performance.views===null?'Chưa có dữ liệu':number(performance.views)}));
  const label=attempt?.busy?'Đang gửi…':runState?.status==='completed'?'Đã tạo xong':runState&&['queued','preparing','running'].includes(runState.status)?'Đang tạo nội dung':runState?.status==='needs_review'?'Cần kiểm tra':'Chạy idea';
  const run=el('div',{class:'idea-library-run'},button(label,()=>void startIdea(idea),{style:'primary',iconName:'plus',disabled:!!blocker,title:blocker||'Tạo nội dung cho idea này','aria-label':'Chạy idea '+(text(idea.filename)||id)}),attempt?.error?el('p',{class:'idea-library-run-error',role:'alert',text:attempt.error}):null);
  const cancel=runState?cancellations.get(String(runState.id)):null;
  if(runState?.can_cancel===true)run.append(button(cancel?.busy?'Đang dừng…':'Dừng chạy',()=>void cancelRun(runState),{small:true,disabled:cancel?.busy===true,'aria-label':'Dừng chạy idea '+(text(idea.filename)||id)}));
  if(cancel?.error)run.append(el('p',{class:'idea-library-run-error',role:'alert',text:cancel.error}));
  const details=disclosure('idea-'+id,'Chi tiết idea',()=>[el('p',{text:text(runState?.message)||blocker||'Tạo nội dung khi bạn bấm. Caption chỉ chứa link áo.'}),el('p',{class:'idea-library-muted',text:performance.known+' / '+total+' video có dữ liệu'}),el('p',{class:'idea-library-muted',text:'Lượt xem là số đã đọc của video thuộc idea, chưa chuẩn hóa tuổi bài hoặc độ lớn Page. Thiếu dữ liệu không được tính thành 0.'}),el('dl',{class:'idea-library-definitions'},definition('Mã idea',id),definition('Ngày thêm',dateTime(idea.created_at,LIBRARY_ZONE)),definition('Trạng thái',STATUS[idea.campaign_state||idea.status]?.[0]||'Chưa xác định'),...(runState?[definition('Lượt chạy',String(runState.id))]:[])),...(runState?.can_cancel===true?[el('p',{text:'Dừng agent không gỡ sản phẩm đã upload hoặc lịch Reel đã lưu.'})]:[])]);
  return el('article',{class:'idea-library-card','aria-label':'Idea '+(text(idea.filename)||id)},preview,el('div',{class:'idea-library-card-body'},title,product,progress,el('progress',{max:total,value:done,'aria-label':'Tiến độ video của '+(text(idea.filename)||id)}),views,rollingPanel(runState,total),run,details));
 }
 function videoCard(asset){
  const linked=Array.isArray(asset.linked_jobs)?asset.linked_jobs:jobs.filter(job=>String(job.asset_id)===String(asset.id)),published=asset.has_published_jobs??linked.some(job=>job.status==='completed'),linkedCount=asset.linked_job_count??linked.length,preview=assetUrl(asset),visual=preview?el('video',{class:'idea-library-video',src:preview,controls:true,preload:'metadata','aria-label':'Xem video '+text(asset.name)}):el('div',{class:'idea-library-no-video'},icon('alert',25),el('p',{text:'Chưa có đường dẫn xem video hợp lệ'}));
  const active=linked.some(job=>!['cancelled','completed'].includes(job.status)),cancelledOnly=linkedCount>0&&linkedCount<=linked.length&&linked.every(job=>job.status==='cancelled'),usage=published?'Đã đăng':active?'Đã lên lịch':cancelledOnly?'Lịch đã hủy':linkedCount?'Đã có trong lịch':'Chưa đặt lịch';
  const body=el('div',{class:'idea-library-card-body'},el('div',{class:'idea-library-card-title'},el('h3',{text:text(asset.name)||'Video đã lưu'}),badge(usage,published?'success':'neutral')));
  const featured=linked.find(job=>job.status!=='cancelled')||linked[0];
  if(featured){const caption=productUrl(featured.caption),pageName=featured.page_name||'Page '+featured.page_id;body.append(el('div',{class:'idea-library-video-featured'},el('strong',{text:featured.status==='cancelled'?'Lịch đã hủy · '+pageName:pageName}),caption?el('a',{href:caption,target:'_blank',rel:'noopener noreferrer',text:caption}):el('p',{class:'idea-library-saved-caption',text:featured.caption||'(Không có caption)'})))}
  body.append(disclosure('video-'+asset.id,'Chi tiết video'+(linkedCount?' · '+linkedCount+' lịch':''),()=>[el('dl',{class:'idea-library-definitions'},definition('Dung lượng',fileSize(asset.size)),definition('Ngày thêm',dateTime(asset.created_at,'America/Los_Angeles')),definition('Mã video',String(asset.id))),...linked.slice(0,3).map(job=>el('div',{class:'idea-library-video-job'},el('strong',{text:job.page_name||'Page '+job.page_id}),el('small',{text:jobLabel(job)+' · '+(job.publish_mode==='on_approval'?'Đăng ngay sau khi bạn duyệt':dateTime(job.scheduled_at,job.timezone||'America/Los_Angeles'))}),el('small',{text:'Lịch #'+job.id}),el('p',{class:'idea-library-saved-caption',text:job.caption||'(Không có caption)'}))),...(linkedCount>linked.length?[el('p',{class:'idea-library-muted',text:'Đang hiển thị '+linked.length+' lịch gần nhất. Mở Lịch đăng Page để xem toàn bộ.'})]:[])]));
  return el('article',{class:'idea-library-card','aria-label':'Video '+(text(asset.name)||asset.id)},el('div',{class:'idea-library-video-visual'},visual),body);
 }
 async function uploaderAction(kind,body){
  if(uploaderBusy)return;uploaderBusy=kind;uploaderError='';uploaderNotice='';
  const provider=kind.startsWith('check-')?body.provider:null;
  if(provider)uploaderChecks.set(provider,{pending:true,message:'Đang kiểm tra đúng shop blueox179.com…'});
  else uploaderChecks.delete('web');
  renderConnections();
  try{
   const value=kind==='connect'?await api.connectUploader(body):kind==='disconnect'?await api.disconnectUploader():await api.checkUploaderSession(provider);
   if(value.upload)data.upload=value.upload;
   if(provider){
    const state=value.upload?.providers?.[provider]||(provider==='local'?value.upload:{}),check=value.check||{},authenticated=typeof check.authenticated==='boolean'?check.authenticated:state?.authenticated;
    const ready=check.ready===true||state?.ready===true;
    const message=authenticated===true?'Phiên shop Blueox hợp lệ.'+(ready?'':' Chưa sẵn sàng sản xuất; xem điều kiện bên dưới.'):authenticated===false?'Phiên shop Blueox chưa hợp lệ. Mở tool để đăng nhập rồi kiểm tra lại.':'Tool chưa xác nhận được phiên shop còn hiệu lực. Mở tool đã cập nhật rồi kiểm tra lại.';
    uploaderChecks.set(provider,{pending:false,ok:authenticated===true,message,checkedAt:text(check.checked_at)||new Date().toISOString()});
   }else uploaderNotice=kind==='connect'?'Đã kết nối tool web. Kiểm tra phiên shop nếu trạng thái còn yêu cầu.':'Đã ngắt phiên tool web trên máy này.';
  }
  catch(reason){
   if(provider){const messages={uploader_check_web_not_connected:'Chưa kết nối tool web. Dùng Kết nối web dự phòng bên dưới.',uploader_check_tool_unavailable:'Chưa kết nối được tool này. Mở tool rồi kiểm tra lại.',uploader_check_wrong_shop:'Tool đang chọn shop khác. Chọn blueox179.com trong tool rồi kiểm tra lại.',uploader_check_tool_session_expired:'Phiên đăng nhập tool đã hết hạn. Mở tool hoặc kết nối web lại.',uploader_check_busy_or_shop_changed:'Tool đang bận hoặc shop vừa thay đổi. Đọc lại trạng thái rồi kiểm tra lại.',uploader_check_timeout:'Kiểm tra phiên hết thời gian chờ. Mở tool để đối chiếu rồi kiểm tra lại.',uploader_check_invalid_response:'Tool chưa trả kết quả kiểm tra phiên hợp lệ. Mở tool để đối chiếu.',uploader_check_session_changed:'Phiên tool vừa thay đổi. Kiểm tra lại phiên hiện tại.'};uploaderChecks.set(provider,{pending:false,ok:false,message:messages[reason.code]||'Chưa kiểm tra được phiên shop. Mở tool để kiểm tra đăng nhập rồi thử lại.',checkedAt:new Date().toISOString()})}
   else uploaderError=safeMessage(reason);
  }
  finally{uploaderBusy='';if(mounted&&section==='connections')renderConnections();void refreshAfterWrite()}
 }
 function uploaderControls(upload){
  const host=el('div',{class:'idea-uploader-controls'}),providers=upload.providers||{},busy=!!uploaderBusy;
  host.append(el('p',{class:'field-hint',text:'Ưu tiên cục bộ khi sẵn sàng; dùng web dự phòng nếu cục bộ chưa dùng được. Mỗi lượt chạy giữ nguyên nơi upload đã chọn. Nút kiểm tra chỉ xác minh hoặc gia hạn phiên shop trong tool, chưa tạo sản phẩm, video hay lịch đăng.'}));
  for(const [kind,title,url] of [['local','Cục bộ · 8770','http://127.0.0.1:8770'],['web','Web dự phòng','https://ul.eagleglobal2023.com/s/blueox179.com/']]){
   const value=providers[kind]||{},check=uploaderChecks.get(kind),label=kind==='local'?'Kiểm tra phiên cục bộ':'Kiểm tra phiên web';
   const state=value.ready?'Sẵn sàng':value.available&&value.active_domain!=='blueox179.com'?'Tool đang chọn shop khác':value.available&&value.authenticated===false?'Phiên shop chưa hợp lệ':value.available?'Đã kết nối · cần kiểm tra shop':kind==='web'&&value.session_saved?'Đã lưu phiên · cần kiểm tra kết nối':kind==='local'?'Chưa mở tool':'Chưa kết nối';
   const details=el('div',{},el('strong',{text:title+(upload.provider===kind?' · Đang chọn':'')}),el('small',{text:state}),blockers(value.blockers));
   if(check)details.append(el('p',{class:check.ok?'idea-library-notice':'field-hint',role:'status','aria-live':'polite',text:(check.pending?'':'Lần kiểm tra gần nhất: ')+check.message}),check.checkedAt?el('small',{text:dateTime(check.checkedAt,'America/Los_Angeles')}):null);
   host.append(el('div',{class:'idea-uploader-provider'},details,el('a',{class:'button small',href:url,target:'_blank',rel:'noopener noreferrer',text:'Mở tool'}),button(uploaderBusy==='check-'+kind?'Đang kiểm tra…':label,()=>void uploaderAction('check-'+kind,{provider:kind}),{small:true,disabled:busy,'aria-label':label})));
  }
  if(uploaderError)host.append(el('p',{class:'idea-library-error',role:'alert',text:uploaderError}));
  if(uploaderNotice)host.append(el('p',{class:'idea-library-notice',role:'status',text:uploaderNotice}));
  const username=el('input',{type:'text',required:true,maxlength:256,autocomplete:'username','aria-label':'Tài khoản tool web',disabled:busy}),password=el('input',{type:'password',required:true,maxlength:1024,autocomplete:'current-password','aria-label':'Mật khẩu tool web',disabled:busy});
  const form=el('form',{class:'idea-uploader-login',onSubmit:event=>{event.preventDefault();if(uploaderBusy)return;const body={username:username.value,password:password.value};password.value='';void uploaderAction('connect',body)}},el('p',{class:'field-hint',text:'Dùng tài khoản đăng nhập ul.eagleglobal2023.com. Mật khẩu chỉ gửi để đăng nhập; phiên tool được lưu mã hóa trên máy này.'}),el('div',{class:'idea-uploader-fields'},el('label',{},'Tài khoản tool web',username),el('label',{},'Mật khẩu tool web',password)),el('div',{class:'idea-library-toolbar-actions'},el('button',{type:'submit',class:'button primary',disabled:busy,text:uploaderBusy==='connect'?'Đang kết nối…':'Kết nối web'}),providers.web?.session_saved?button('Ngắt kết nối web',()=>void uploaderAction('disconnect'),{small:true,disabled:busy}):null));
  host.append(el('details',{class:'idea-uploader-connect',open:uploaderBusy==='connect'},el('summary',{text:'Kết nối web dự phòng'}),form));return host;
 }
 function renderConnections(){
  if(!mounted||section!=='connections'||!contentHost)return;contentHost.replaceChildren();
  const p=data.pipeline,settings=p.settings||{},policy=data.policy||{},upload=data.upload||{},video=data.video_agent||{},source=productUrl(policy.source_requested||settings.campaign_source?.campaign_url)||SOURCE,blackSource=productUrl(policy.black_source_requested||settings.black_campaign_source?.campaign_url)||BLACK_SOURCE;
  const sourceRow=(label,url,value)=>el('div',{class:'idea-library-source-row'},el('div',{},el('strong',{text:label}),el('a',{href:url,target:'_blank',rel:'noopener noreferrer',text:url})),badge(value?.verified===true&&productUrl(value?.campaign_url)===url?'Đã xác minh':'Chưa xác minh',value?.verified===true&&productUrl(value?.campaign_url)===url?'success':'neutral'));
  contentHost.append(el('div',{class:'idea-library-connections-heading'},el('div',{},el('h2',{text:'Kết nối sản xuất nội dung'}),el('p',{text:'Bấm Chạy idea để chạy thủ công, hoặc chủ động bật tự động cho idea bạn thêm.'})),button('Đọc lại trạng thái',()=>void load(),{iconName:'refresh',disabled:!!reading})),el('section',{class:'panel idea-library-settings'},el('div',{class:'panel-body'},el('label',{},'Kho idea cố định',el('input',{readonly:true,value:text(p.directory)||DIRECTORY,'aria-label':'Thư mục kho idea'})),sourceRow('File chữ đen · zitat25',source,settings.campaign_source),sourceRow('File chữ trắng · ge2610',blackSource,settings.black_campaign_source),el('p',{class:'idea-library-caption-policy',text:'Caption cho video mới: chỉ link sản phẩm của chính idea, không thêm câu chữ hoặc hashtag.'}))));
  const card=(title,state,details,problems)=>el('section',{class:'panel'},panelHeading(title,''),el('div',{class:'panel-body'},state,el('dl',{class:'idea-library-definitions'},details),blockers(problems)));
  const uploadCard=card('Upload Podsach',badge(upload.ready===true?'Sẵn sàng upload':'Chưa sẵn sàng upload',upload.ready===true?'success':'warning'),[definition('Tool',text(upload.tool_root)||'PODSach bulk prep'),definition('Kết nối',upload.available===true?'Đã đọc được tool':'Chưa kết nối tool'),definition('Shop đang chọn',text(upload.active_domain)||'Chưa xác định'),definition('Phiên blueox179',upload.authenticated===true?'Đã xác nhận đăng nhập':upload.authenticated===false?'Chưa đăng nhập':'Chưa xác minh phiên đăng nhập')],upload.blockers);
  if(upload.providers)uploadCard.querySelector('.panel-body').append(uploaderControls(upload));
  producerConnections.adopt(data?.producers);
  telegramApproval.adopt(data?.telegram);
  ideaAutomation.adopt(data?.automation);
  audioMixes.adopt(data?.audio_mixes);
  projectAssets.adopt(data?.project_assets);
  contentHost.append(ideaAutomation.node,telegramApproval.node,audioMixes.node,projectAssets.node,producerConnections.node,orchestrationPanel((data.execution||p.execution)?.orchestration),el('div',{class:'idea-library-workers'},uploadCard,card('Video · project Page',badge(video.status==='ready'||video.ready===true?'Đã xác nhận bàn giao':'Chờ xác nhận bàn giao',video.status==='ready'||video.ready===true?'success':'warning'),[definition('Chat',text(video.title)||'Tạo video theo kế hoạch đã chốt'),definition('Quy trình','Một ảnh riêng → một video zoom 8 giây'),...continuityDefinitions(video),definition('Skill',text(video.skill||video.skill_path)||'Đang xác nhận'),definition('Trạng thái',text(video.message)||'Định dạng bàn giao được giữ riêng; chưa chạy batch')],video.blockers)));
 }
 function rankingPanel(value){
  const remote=Number.isFinite(value.total),cohorts=list(value.cohorts),rows=cohorts.flatMap(cohort=>list(cohort.items).map(row=>({...row,metric:cohort.metric}))),details=el('details',{class:'idea-library-ranking',open:rankingOpen,onToggle:event=>{const changed=rankingOpen!==event.currentTarget.open;rankingOpen=event.currentTarget.open;if(changed&&rankingOpen&&data?.pipeline?.paged)windowChanged()}},el('summary',{},icon('activity',16),'Hiệu quả idea · lượt xem gần 24 giờ'));
  details.append(el('div',{class:'idea-library-ranking-actions'},button(refreshingViews?'Đang đọc lượt xem…':'Cập nhật lượt xem',()=>void refreshViews(),{iconName:'activity',small:true,disabled:refreshingViews,'data-action':'refresh-views'})),el('p',{text:'So cùng loại lượt xem và tuổi bài; chỉ so mức tham chiếu Page khi có dữ liệu. Idea mới vẫn có lượt thử. Xếp hạng đang là bản xem trước, chưa tự phân bổ sản xuất.'}));
  if(data?.pipeline?.paged&&!remote){details.append(el('p',{class:'idea-library-muted',text:'Thống kê được đọc khi mở mục này.'}));return details}
  if(!rows.length){paging.ranking.page=1;details.append(el('p',{class:'idea-library-muted',text:'Chưa đủ dữ liệu lượt xem. Thiếu dữ liệu không được tính thành 0 view.'}));return details}
  const host=el('div',{class:'idea-library-ranking-results'});details.append(host);
  function draw(){
   const table=el('table',{}),body=el('tbody');table.append(el('thead',{},el('tr',{},['Idea / loại view','Số video','View gần 24h','So với Page'].map(label=>el('th',{scope:'col',text:label})))));
   for(const row of remote?rows:paged(rows,'ranking')){const views=typeof row.median_views_24h==='number'&&Number.isFinite(row.median_views_24h)?row.median_views_24h:null,lift=typeof row.median_page_lift==='number'&&Number.isFinite(row.median_page_lift)?row.median_page_lift:null;body.append(el('tr',{},el('td',{},el('strong',{text:text(row.filename||row.idea_id)||'Idea'}),el('small',{text:text(row.metric)||'Chưa rõ loại view'})),el('td',{text:number(row.sample_count)}),el('td',{text:views===null?'Chưa có dữ liệu':number(views)}),el('td',{text:lift===null?'Chưa có mức tham chiếu':lift.toLocaleString('vi-VN',{maximumFractionDigits:2})+' × mức tham chiếu'})))}table.append(body);host.replaceChildren(el('div',{class:'table-wrap'},table),pager('ranking',remote?value.total:rows.length,draw));
  }
  draw();return details;
 }
 return {
  mount(host){if(mounted&&host.contains(root))return;mounted=true;syncToday();host.append(root);render();void load();poll=setInterval(()=>document.visibilityState==='visible'?load({automatic:true}):undefined,10000)},
  refresh(){return load()},
  unmount(){if(!mounted)return;ideaAutomation.dispose();telegramApproval.dispose();audioMixes.dispose();producerConnections.dispose();projectAssets.dispose();releaseVideos();root.replaceChildren();contentHost=null;lastRenderedFingerprint='';mounted=false;sequence++;cancelReads();clearTimeout(searchTimer);clearInterval(poll);poll=null;reading=false;loading=false},
  isMounted(host){return mounted&&host.contains(root)},
 };
}
