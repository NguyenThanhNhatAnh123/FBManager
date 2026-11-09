import {api,initialize,APP_BASE} from './api.js';
import {el,icon,button,badge,empty,errorBox,skeleton,toast,announce,dateTime,relative,number,avatar,facebookUrl,openDialog,safeMessage} from './ui.js';
import {showPageEditor} from './page-editor.js';
import {createTokenReelScheduler} from './reel-scheduler.js';
import {createVideoSource} from './video-source.js';
import {createIdeaPipeline} from './idea-pipeline.js';
import {createPagination,pageWindow} from './pagination.js';
import {canPublishPage} from './posting-policy.js';

const STATUS={unchecked:['Chưa kiểm tra','neutral'],working:['Hoạt động','success'],permission_limited:['Thiếu một phần quyền','warning'],api_access_blocked:['Meta chặn API','danger'],invalid:['Token không hợp lệ','danger'],rate_limited:['Giới hạn truy cập','warning'],error:['Cần kiểm tra','danger'],disconnected:['Đã ngắt kết nối','neutral'],active:['Khả dụng','success'],needs_attention:['Cần chú ý','warning']};
const TYPE={user:'Người dùng',system_user:'System user',page:'Page'};
const VIEWS={pages:['Pages','Một dòng cho mỗi Page, dù được quản lý qua nhiều token.','pages'],schedule:['Lịch đăng Page','Chọn Page, video và thời gian cho lịch Reel mới.','calendar'],saved:['Lịch đã lưu','Xem video và caption. Bấm vào thẻ để mở chi tiết lịch.','clock'],settings:['Settings','Quản lý kết nối, dữ liệu và nhật ký của Page Manager.','settings']};
const VIEW_PATHS={pages:'/',schedule:'/schedule',saved:'/saved',settings:'/settings'};
const SETTINGS={overview:'Tổng quan',connections:'Kết nối token',activity:'Hoạt động',videosource:'Nguồn video',ideapipeline:'Kho nội dung'};
const state={view:'pages',settingsSection:'overview',info:{},ready:false,timezone:'Asia/Ho_Chi_Minh',connections:[],connectionsMeta:null,connectionsLoading:false,connectionOptions:[],optionsMeta:null,optionsFor:null,optionsSearch:'',optionsOffset:0,optionsError:'',pages:[],pagesMeta:null,pagesFor:null,overview:null,events:[],eventsMeta:null,eventsLoading:false,errors:{},loading:true,busy:new Set(),search:'',connection:'',informationStatus:'',today:'',sources:new Map(),drawer:null};
const paging={pages:{page:1,pageSize:10},connections:{page:1,pageSize:10},activity:{page:1,pageSize:10}},postPaging=new Map();
const viewReads=new Map();
let detailSequence=0,detailController=null,searchTimer=null,optionTimer=null;
const app=document.querySelector('#app'),main=el('main',{class:'main',id:'main-content',tabindex:'-1'}),content=el('div',{id:'view-content'}),breadcrumb=el('strong',{text:'Pages'}),syncLabel=el('span',{class:'sync-label',text:'Đang kết nối…'}),navButtons=new Map();
const connectionDialog=document.querySelector('#connection-dialog'),pageDialog=document.querySelector('#page-dialog'),confirmDialog=document.querySelector('#confirm-dialog');
const tokenReels=createTokenReelScheduler({onEditPage:editPostingPage});
const videoSource=createVideoSource({onSchedule:()=>navigate('schedule'),onEditPage:editPostingPage});
const ideaPipeline=createIdeaPipeline({onSchedule:()=>navigate('schedule')});

function statusBadge(value){const [label,tone]=STATUS[value]||['Chưa xác định','neutral'];return badge(label,tone)}
function informationBadge(page){const changed=page.information_status==='updated';return el('span',{title:canPublishPage(page)?'Đã có xác nhận đổi thông tin theo đúng Page ID; quyền nguồn vẫn được kiểm tra riêng.':'Chưa có xác nhận đổi thông tin cho đúng Page ID; chưa được bật đăng.'},badge(changed?'Đã đổi thông tin':'Chưa đổi thông tin',changed?'success':'neutral'))}
function editPostingPage(page){navigate('pages');showPageEditor({page,connections:connectionsOf(page),initialConnectionId:state.sources.get(String(page.id))||'',onRefresh:({pageId,connectionId})=>{state.sources.set(String(pageId),String(connectionId));return refreshDetail(pageId)},onChanged:()=>void reloadPageViews()})}
function canonicalPages(values){return [...new Map((values||[]).filter(row=>row&&row.id).map(row=>[String(row.id),row])).values()]}
function connectionsOf(page){return [...new Map((page.connections||[]).map(row=>[String(row.id),row])).values()]}
function countToday(page){if(page.posts_today===null||page.posts_today===undefined)return {label:'Chưa kiểm tra',unknown:true};if(!page.posts_today_complete&&page.posts_today===0)return {label:'Chưa đủ dữ liệu',unknown:true};return {label:(page.posts_today_complete?'':'≥ ')+number(page.posts_today),partial:!page.posts_today_complete}}
function shortCategory(page){return page.category||'Chưa có danh mục'}
function busy(key){return state.busy.has(key)}
function focusView(){main.focus({preventScroll:true})}
function listMeta(value){return Number.isSafeInteger(value?.total)&&value.total>=0&&Number.isSafeInteger(value?.offset)&&value.offset>=0?{total:value.total,offset:value.offset,limit:value.limit}:null}
function listPage(values,key){const setting=paging[key],meta=state[key+'Meta'],view=pageWindow(meta?.total??values.length,setting.page,setting.pageSize);setting.page=view.page;const refresh=()=>meta?key==='pages'?void loadPages():void loadConnections():key==='pages'?renderPageRows():render();return {visible:meta?values:values.slice(view.start,view.end),pager:createPagination({label:key==='pages'?'Pages':'Kết nối token',...view,onPageChange:page=>{setting.page=page;refresh()},onPageSizeChange:pageSize=>{setting.pageSize=pageSize;setting.page=1;refresh()}})}}

function shell(){
  const sidebar=el('aside',{class:'sidebar'},el('div',{class:'brand'},el('span',{class:'brand-mark'},icon('pages',23)),el('div',{},el('strong',{text:'Page Manager'}),el('small',{text:'Meta workspace'}))));
  sidebar.append(el('div',{class:'app-switcher','aria-label':'Chuyển ứng dụng'},el('a',{href:APP_BASE==='/pages'?'/':'http://127.0.0.1:8000/','aria-label':'Mở Chrome Manager',title:'Chrome'},icon('globe',15),el('span',{class:'switch-label',text:'Chrome'})),el('span',{'aria-current':'page','aria-label':'Page Manager',title:'Page Manager'},icon('pages',15),el('span',{class:'switch-label',text:'Page Manager'}))));
  const nav=el('nav',{class:'nav','aria-label':'Điều hướng chính'});
  for(const [view,[label,,symbol]] of Object.entries(VIEWS).filter(([view])=>view!=='settings')){
    const item=el('button',{type:'button','aria-label':label,onClick:()=>navigate(view)},icon(symbol,19),el('span',{text:label}));
    if(view==='pages')item.append(el('span',{class:'nav-count',id:'nav-page-count',text:'—'}));
    navButtons.set(view,item);nav.append(item);
  }
  const settings=el('button',{type:'button','aria-label':'Settings',onClick:()=>navigate('settings')},icon('settings',19),el('span',{text:'Settings'}));navButtons.set('settings',settings);
  sidebar.append(el('div',{class:'nav-caption',text:'Không gian làm việc'}),nav,
    el('div',{class:'sidebar-bottom'},el('nav',{class:'nav nav-bottom','aria-label':'Cài đặt ứng dụng'},settings),el('a',{href:'/auth/account',class:'sidebar-footer',text:'Tài khoản & đăng xuất'}),el('div',{class:'sidebar-footer'},el('span',{text:'Page Manager'}),el('span',{id:'app-version',text:'v1.0'}))));
  const topbar=el('header',{class:'topbar'},el('div',{class:'breadcrumb'},icon('grid',14),el('span',{text:'Không gian của bạn'}),icon('chevron',12),breadcrumb),el('div',{class:'top-right'},syncLabel,el('span',{class:'read-only'},icon('shield',13),'Meta workspace')));
  main.append(content);app.append(sidebar,el('div',{class:'workspace'},topbar,main));
}

function settingsSection(section){
  return Object.hasOwn(SETTINGS,section)&&!(section==='ideapipeline'&&state.info.idea_pipeline===false)?section:'overview';
}

function locationView(){
  const path=location.pathname.slice(APP_BASE.length).replace(/\/$/,'')||'/';
  const view=Object.keys(VIEW_PATHS).find(key=>VIEW_PATHS[key]===path)||'pages';
  const tab=new URLSearchParams(location.search).get('tab');
  return {view,section:settingsSection(tab)};
}

function currentReads(){
  if(state.view==='pages')return ['pages','options'];
  if(state.view!=='settings')return [];
  const key={overview:'overview',connections:'connections',activity:'events'}[state.settingsSection];
  return key?[key]:[];
}

function cancelViewRead(key){
  const pending=viewReads.get(key);
  if(!pending)return;
  viewReads.delete(key);
  pending.controller.abort();
}

function readView(key,query,work){
  if(!state.ready||!currentReads().includes(key))return Promise.resolve();
  const signature=JSON.stringify(query),existing=viewReads.get(key);
  if(existing?.signature===signature)return existing.promise;
  cancelViewRead(key);
  const pending={signature,controller:new AbortController()};
  const current=()=>viewReads.get(key)===pending;
  viewReads.set(key,pending);
  pending.promise=Promise.resolve().then(()=>current()?work(pending.controller.signal,current):undefined)
    .finally(()=>{if(current())viewReads.delete(key)});
  return pending.promise;
}

function navigate(view,{section=state.settingsSection,historyMode='push',load=true}={}){
  if(!VIEWS[view])return;
  section=settingsSection(section);
  const changed=view!==state.view||view==='settings'&&section!==state.settingsSection;
  state.view=view;state.settingsSection=section;
  const activeReads=currentReads();
  for(const key of viewReads.keys())if(!activeReads.includes(key))cancelViewRead(key);
  if(view!=='pages'){clearTimeout(searchTimer);clearTimeout(optionTimer);if(pageDialog.open)pageDialog.close()}
  if(!['schedule','saved'].includes(view))tokenReels.unmount();
  if(view!=='settings'||state.settingsSection!=='videosource')videoSource.unmount();
  if(view!=='settings'||state.settingsSection!=='ideapipeline')ideaPipeline.unmount();
  const path=APP_BASE+VIEW_PATHS[view]+(view==='settings'&&section!=='overview'?'?tab='+section:'');
  if(historyMode&&path!==location.pathname+location.search){
    if(historyMode==='replace')history.replaceState(null,'',path);
    else history.pushState(null,'',path);
  }
  for(const [key,item] of navButtons){if(key===view)item.setAttribute('aria-current','page');else item.removeAttribute('aria-current')}
  breadcrumb.textContent=VIEWS[view][0];render();focusView();
  if(changed&&load&&state.ready)void loadVisible();
}

function navigateSettings(section='overview'){navigate('settings',{section})}

function navigateLocation({historyMode=null,load=true}={}){
  const {view,section}=locationView();
  navigate(view,{section,historyMode,load});
}

function heading(){
  const [title,description]=VIEWS[state.view];
  return el('div',{class:'page-heading'},el('div',{},el('div',{class:'eyebrow',text:'PAGE WORKSPACE'}),el('h1',{text:title}),el('p',{text:description})),el('div',{class:'heading-actions'},state.view==='saved'?button('Tạo lịch mới',()=>navigate('schedule'),{style:'primary',iconName:'plus'}):state.view==='schedule'?button('Xem lịch đã lưu',()=>navigate('saved'),{iconName:'clock'}):button('Làm mới',()=>void loadVisible({explicit:true}),{iconName:'refresh',disabled:state.loading,title:'Tải lại dữ liệu đã lưu, không gọi đồng bộ Meta'}),state.view==='settings'&&state.settingsSection==='connections'?button('Thêm kết nối',()=>showConnectionForm(),{style:'primary',iconName:'plus'}):null));
}

function footer(){return el('footer',{class:'bottom-note'},el('span',{},'Page Manager độc lập · ',state.info.graph_version||'Meta Graph API'),el('span',{class:'live-small'},el('i',{class:'dot'}),'Dữ liệu theo múi giờ ',state.timezone))}

function render(){
  if(['schedule','saved'].includes(state.view)&&tokenReels.isMounted(content,state.view==='saved'?'saved':'create'))return;
  if(state.view==='settings'&&state.settingsSection==='videosource'&&videoSource.isMounted(content))return;
  if(state.view==='settings'&&state.settingsSection==='ideapipeline'&&ideaPipeline.isMounted(content))return;
  content.replaceChildren(heading());
  const errorKeys=currentReads().filter(key=>key!=='options'&&key!=='pages');
  for(const key of errorKeys)if(state.errors[key])content.append(errorBox(state.errors[key],()=>void loadVisible({explicit:true})));
  if(state.loading){content.append(skeleton(6),footer());return}
  if(state.view==='pages')renderPages();
  else if(state.view==='settings')renderSettings();
  else renderSchedule();
  content.append(footer());
}

function renderSettings(){
  if(state.info.idea_pipeline===false&&state.settingsSection==='ideapipeline')state.settingsSection='overview';
  const tabs=el('div',{class:'settings-tabs',role:'tablist','aria-label':'Các mục cài đặt','aria-orientation':'horizontal'}),entries=Object.entries(SETTINGS).filter(([key])=>key!=='ideapipeline'||state.info.idea_pipeline!==false);
  for(const [key,label] of entries){const tab=el('button',{type:'button',id:'settings-tab-'+key,role:'tab','aria-selected':state.settingsSection===key?'true':'false','aria-controls':'settings-panel',tabindex:state.settingsSection===key?'0':'-1',onClick:()=>navigateSettings(key),onKeydown:event=>{const offset=['ArrowRight','ArrowDown'].includes(event.key)?1:['ArrowLeft','ArrowUp'].includes(event.key)?-1:0;if(!offset&&event.key!=='Home'&&event.key!=='End')return;event.preventDefault();const index=event.key==='Home'?0:event.key==='End'?entries.length-1:(entries.findIndex(([value])=>value===key)+offset+entries.length)%entries.length;navigateSettings(entries[index][0]);document.querySelector('#settings-tab-'+entries[index][0])?.focus()}},icon(key==='connections'?'key':key==='activity'?'activity':'settings',16),el('span',{text:label}));tabs.append(tab)}
  const section=el('section',{class:'settings-section',id:'settings-panel',role:'tabpanel','aria-labelledby':'settings-tab-'+state.settingsSection});content.append(el('div',{class:'workspace-settings'},tabs,section));
  if(state.settingsSection==='videosource'){videoSource.mount(section);return}
  if(state.settingsSection==='ideapipeline'){ideaPipeline.mount(section);return}
  if(state.settingsSection==='connections'){renderConnections(section);return}
  if(state.settingsSection==='activity'){renderActivity(section);return}
  if(state.info.idea_pipeline===false)section.append(el('p',{class:'section-note',text:'Kho nội dung và trình tải lên PODSach chỉ có trong không gian chính. Bạn có thể tải video lên trực tiếp trong Lịch đăng Page.'}));
  section.append(el('div',{class:'settings-summary'},
    el('section',{class:'panel'},panelHeading('Kết nối token','Quản lý danh tính, quyền và nguồn dữ liệu.'),el('div',{class:'panel-body settings-intro'},el('p',{class:'section-note',text:number(state.connectionsMeta?.total??state.overview?.stats?.saved_connections??state.overview?.stats?.connections??state.connections.length)+' kết nối đã lưu. Kiểm tra và đồng bộ chỉ đọc dữ liệu mà từng token được cấp quyền.'}),button('Quản lý kết nối token',()=>navigateSettings('connections'),{iconName:'key'}))),
    el('section',{class:'panel'},panelHeading('Nhật ký hoạt động','Theo dõi kết quả kiểm tra và đồng bộ.'),el('div',{class:'panel-body settings-intro'},el('p',{class:'section-note',text:'Nhật ký giữ thông báo an toàn, không hiển thị token. Dữ liệu đọc thiếu được ghi rõ thay vì coi là 0.'}),button('Xem nhật ký hoạt động',()=>navigateSettings('activity'),{iconName:'activity'}))),
    el('section',{class:'panel'},panelHeading('Dữ liệu & không gian','Một địa chỉ truy cập, dữ liệu được giữ riêng.'),el('div',{class:'panel-body settings-intro'},el('p',{class:'section-note',text:'Page Manager dùng cơ sở dữ liệu và phiên riêng với Chrome Manager. Chuyển ứng dụng không sao chép profile, proxy hoặc token giữa hai bên.'}),el('dl',{class:'detail-grid'},el('div',{},el('dt',{text:'Chế độ dữ liệu'}),el('dd',{text:'Đọc dữ liệu · Chỉnh sửa Page khi bạn lưu'})),el('div',{},el('dt',{text:'Múi giờ tính bài hôm nay'}),el('dd',{text:state.timezone})),el('div',{},el('dt',{text:'Phiên bản Graph API'}),el('dd',{text:state.info.graph_version||'Chưa xác định'})),el('div',{},el('dt',{text:'Bảo vệ token'}),el('dd',{text:'Chỉ nhập; không hiển thị lại hoặc lưu ở trình duyệt'})))))));
}

function renderSchedule(){
  tokenReels.mount(content,state.info,state.view==='saved'?'saved':'create');
}

function panelHeading(title,description,action){return el('div',{class:'panel-heading'},el('div',{},el('h2',{text:title}),el('p',{text:description})),action)}
function coverageNote(){return el('div',{class:'coverage-note'},icon('info',14),el('span',{text:'Bài hôm nay được tính từ cache đã đọc, theo '+state.timezone+'. “≥” là số tối thiểu; thiếu hoặc cũ dữ liệu không được tính thành 0.'}))}

function sourceChips(page){
  const items=connectionsOf(page),box=el('div',{class:'chips'});
  for(const source of items.slice(0,2))box.append(el('span',{class:'chip',title:source.label+' · '+(STATUS[source.status]?.[0]||'Chưa kiểm tra'),text:source.label}));
  if(items.length>2)box.append(el('span',{class:'chip more-chip',text:'+'+(items.length-2)}));
  if(!items.length)box.append(el('span',{class:'muted',text:'Chưa có nguồn hoạt động'}));
  return box;
}

function pagesTable(rows,compact=false){
  const wrap=el('div',{class:'table-wrap'}),table=el('table',{class:'pages-table'+(compact?' overview-pages':'')});
  table.append(el('thead',{},el('tr',{},['Page / Meta ID','Nguồn token','Tình trạng','Bài hôm nay',''].map(text=>el('th',{scope:'col',text})))));
  const body=el('tbody');
  for(const page of canonicalPages(rows)){
    const today=countToday(page),identity=el('button',{type:'button',class:'page-name-button','aria-label':'Xem chi tiết Page '+page.name,onClick:()=>void openPage(page.id)},el('strong',{text:page.name||'Page chưa có tên'}),el('small',{text:page.id}));
    body.append(el('tr',{},el('td',{class:'identity-column'},el('div',{class:'page-cell'},avatar(page.name,page.id,page.avatar_url),el('div',{},identity,compact?null:el('div',{class:'cell-secondary',text:shortCategory(page)})))),
      el('td',{'data-label':'Kết nối'},sourceChips(page)),el('td',{'data-label':'Tình trạng'},informationBadge(page)),
      el('td',{'data-label':'Bài hôm nay'},el('div',{class:today.unknown?'cell-secondary':'post-number'+(page.posts_today===0?' zero':''),text:today.label}),el('div',{class:'cell-secondary',text:today.partial?'Dữ liệu một phần':page.posts_checked_at?relative(page.posts_checked_at):'Chưa đọc bài viết'})),
      el('td',{},button('Chi tiết',()=>void openPage(page.id),{style:'ghost',small:true,iconName:'chevron','aria-label':'Mở Page '+page.name}))));
  }
  table.append(body);wrap.append(table);return wrap;
}

function renderPages(){
  const panel=el('section',{class:'panel'}),toolbar=el('div',{class:'toolbar'}),filters=el('div',{class:'filters'});
  const search=el('input',{id:'page-search',type:'search','aria-label':'Tìm Page',placeholder:'Tìm tên Page hoặc Meta ID…',value:state.search,onInput:event=>{state.search=event.target.value;paging.pages.page=1;renderPageRows();clearTimeout(searchTimer);searchTimer=setTimeout(()=>void loadPages(),250)}});
  const source=el('select',{'aria-label':'Lọc nguồn token',onChange:event=>{state.connection=event.target.value;paging.pages.page=1;void loadPages()}},el('option',{value:'',text:'Tất cả kết nối'}));
  source.id='page-connection-options';
  const sourceSearch=el('input',{type:'search',placeholder:'Tìm nguồn token…','aria-label':'Tìm nguồn token',value:state.optionsSearch,onInput:event=>{state.optionsSearch=event.target.value;state.optionsOffset=0;clearTimeout(optionTimer);optionTimer=setTimeout(()=>void loadConnectionOptions(),200)}}),sourceGroup=el('div',{class:'page-source-filter'},sourceSearch,source,el('div',{id:'page-connection-pager'}));
  const information=el('select',{'aria-label':'Lọc thông tin Page',onChange:event=>{state.informationStatus=event.target.value;paging.pages.page=1;void loadPages()}},el('option',{value:'',text:'Thông tin Page: tất cả'}),el('option',{value:'updated',text:'Đã đổi thông tin'}),el('option',{value:'not_updated',text:'Chưa đổi thông tin'}));information.value=state.informationStatus;
  const today=el('select',{'aria-label':'Lọc bài hôm nay',onChange:event=>{state.today=event.target.value;paging.pages.page=1;void loadPages()}},el('option',{value:'',text:'Bài hôm nay: tất cả'}),el('option',{value:'posted',text:'Có bài hôm nay'}),el('option',{value:'zero',text:'Đã xác nhận 0 bài'}),el('option',{value:'unknown',text:'Chưa đủ dữ liệu'}));today.value=state.today;
  filters.append(el('div',{class:'search-box'},icon('search',15),search),sourceGroup,information,today);toolbar.append(filters);panel.append(toolbar,el('div',{id:'page-rows'}),coverageNote());content.append(panel);renderSourceOptions();renderPageRows();
}

function filteredPages(){return state.pages.filter(page=>state.today==='posted'?typeof page.posts_today==='number'&&page.posts_today>0:state.today==='zero'?page.posts_today_complete&&page.posts_today===0:state.today==='unknown'?page.posts_today===null||!page.posts_today_complete:true)}
function renderPageRows(){const target=document.querySelector('#page-rows');if(!target)return;if(state.pagesFor!==JSON.stringify(pageQuery())){target.replaceChildren(skeleton(4));return}const rows=state.pagesMeta?state.pages:filteredPages(),{visible,pager}=listPage(rows,'pages');target.replaceChildren(rows.length?pagesTable(visible):empty(state.pages.length?'Không có Page phù hợp':'Chưa có Page trong danh sách','Thử đổi bộ lọc, hoặc đồng bộ một kết nối để tải Page.'));target.append(pager)}

function connectionCard(connection,compact=false){
  const key='connection:'+connection.id,card=el('article',{class:'connection-card','aria-label':'Kết nối '+connection.label});
  card.append(el('div',{class:'connection-top'},el('span',{class:'connection-symbol'},icon('key',19)),el('div',{class:'connection-title'},el('strong',{text:connection.label}),el('small',{text:TYPE[connection.token_type]||'Token'})),statusBadge(connection.status)));
  card.append(el('div',{class:'connection-meta'},el('span',{},el('strong',{text:number(connection.page_count)}),' Page'),el('span',{},'Kiểm tra: ',el('strong',{text:connection.checked_at?relative(connection.checked_at):'Chưa có'})),compact?null:el('span',{},'Hạn token: ',el('strong',{text:connection.expires_at?dateTime(connection.expires_at,state.timezone):'Chưa xác định'}))));
  if(!compact){
    const scope=connection.business_scope_verified===true?'Danh sách Page đã được đối chiếu với BM này.':connection.business_scope_verified===false?'Đồng bộ Page để xác minh phạm vi BM trước khi chỉnh sửa hoặc đặt lịch.':'';
    card.append(el('div',{class:'identity-note'},icon('info',15),el('div',{},el('span',{text:connection.identity_name||'Danh tính chưa được xác nhận'}),connection.identity_id?el('div',{text:'ID: '+connection.identity_id}):null,connection.business_id?el('div',{text:'Business ID đã khai báo: '+connection.business_id}):null,scope?el('div',{text:scope}):null)));
    const permissions=el('div',{class:'connection-permissions'});
    const grants=Array.isArray(connection.permissions)?connection.permissions:connection.permissions&&typeof connection.permissions==='object'?Object.keys(connection.permissions).filter(key=>connection.permissions[key]===true||connection.permissions[key]==='granted'):null;
    if(grants===null)permissions.append(el('span',{class:'cell-secondary',text:'Quyền: chưa có thông tin được xác nhận'}));
    else if(!grants.length)permissions.append(el('span',{class:'cell-secondary',text:'Chưa ghi nhận quyền được cấp'}));
    else for(const grant of grants.slice(0,10)){const name=typeof grant==='string'?grant:grant.permission||'Quyền chưa xác định',status=typeof grant==='object'?grant.status:null;permissions.append(el('span',{class:'permission'+(status&&status!=='granted'?' limited':''),text:name+(status==='declined'?' · chưa cấp':status==='expired'?' · hết hạn':'')}))}
    card.append(permissions);
    if(connection.app_id)card.append(el('div',{class:'cell-secondary',text:'App ID: '+connection.app_id}));
    card.append(el('div',{class:'cell-secondary',text:connection.has_token?'Token đã lưu · không hiển thị lại':'Không còn token đã lưu'}));
  }
  if(connection.error)card.append(el('div',{class:'connection-error',text:connection.error}));
  const actions=el('div',{class:'connection-actions'});
  actions.append(button(busy(key)?'Đang xử lý…':'Kiểm tra',()=>void connectionAction(connection,'check'),{iconName:'shield',small:true,disabled:busy(key)||!connection.connected||!connection.has_token}),button('Đồng bộ Page',()=>void connectionAction(connection,'sync'),{style:'primary',iconName:'refresh',small:true,disabled:busy(key)||!connection.connected||!connection.has_token}));
  if(!compact)actions.append(button('Cập nhật token',()=>showConnectionForm(connection),{small:true,disabled:busy(key)}),button('Gỡ kết nối',()=>confirmRemoval(connection),{small:true,style:'ghost',iconName:'logout',disabled:busy(key)||!connection.connected}));
  card.append(actions);return card;
}

function renderConnections(target=content){
  target.append(el('p',{class:'section-note'},'Token người dùng, system user hoặc Page là các nguồn quyền khác nhau. Kiểm tra xác nhận danh tính; đồng bộ chỉ đọc Page được nguồn đó cho phép. Token ứng dụng APP_ID|APP_SECRET không được hỗ trợ.'));
  if(state.connectionsLoading){target.append(skeleton(4));return}
  if(!state.connections.length){target.append(el('section',{class:'panel'},empty('Kết nối Page đầu tiên','Thêm token của bạn để đọc danh tính và danh sách Page. Token không được trả lại trình duyệt sau khi lưu.',button('Thêm kết nối token',()=>showConnectionForm(),{style:'primary',iconName:'plus'}))));return}
  const {visible,pager}=listPage(state.connections,'connections'),grid=el('div',{class:'connections-grid'});for(const connection of visible)grid.append(connectionCard(connection));target.append(grid,pager);
}

function eventItem(event){const tone=event.status==='error'||event.status==='failed'?'error':event.status==='partial'?'partial':'';return el('div',{class:'activity-item'},el('span',{class:'event-symbol '+tone},icon(tone?'alert':'check',13)),el('div',{class:'event-content'},el('strong',{text:event.connection_label||'Page Manager'}),el('p',{text:event.message||event.action||'Đã ghi nhận hoạt động'}),el('small',{text:dateTime(event.created_at,state.timezone)})))}
function eventQuery(){const {page,pageSize}=paging.activity;return {limit:pageSize,offset:(page-1)*pageSize}}
function acceptEvents(value,query){state.events=value.items||[];state.eventsMeta=Number.isFinite(value.total)&&Number.isInteger(value.offset)?{total:Math.max(0,value.total),offset:value.offset,limit:value.limit||query.limit}:null}
function renderActivity(target=content){
 const setting=paging.activity,total=state.eventsMeta?.total??state.events.length,view=pageWindow(total,setting.page,setting.pageSize);setting.page=view.page;
 const current=!state.eventsMeta||state.eventsMeta.offset===view.start,visible=state.eventsMeta?state.events:state.events.slice(view.start,view.end);
 const panel=el('section',{class:'panel','aria-busy':state.eventsLoading?'true':'false'},panelHeading('Nhật ký hoạt động',state.eventsMeta?'Các sự kiện đã lưu · không chứa token':'Các sự kiện gần nhất đã tải · không chứa token',el('span',{class:'counter',text:number(total)+' sự kiện'})));
 if(state.eventsLoading||!current&&!state.errors.events)panel.append(skeleton(4));
 else if(!current)panel.append(empty('Chưa tải được trang nhật ký','Bấm Thử lại để đọc lại trang đã chọn.'));
 else if(!visible.length)panel.append(empty('Nhật ký đang trống','Kiểm tra hoặc đồng bộ kết nối để ghi nhận hoạt động.'));
 else{const body=el('div',{class:'panel-body'});for(const event of visible)body.append(eventItem(event));panel.append(body)}
 const pager=createPagination({label:'Hoạt động',...view,onPageChange:page=>{setting.page=page;state.eventsMeta?void loadEvents():render()},onPageSizeChange:pageSize=>{setting.pageSize=pageSize;setting.page=1;state.eventsMeta?void loadEvents():render()}});
 if(state.eventsLoading)for(const control of pager.querySelectorAll('button,select'))control.disabled=true;
 panel.append(pager);target.append(panel);
}
function loadEvents(){
 const query=eventQuery();
 return readView('events',query,async(signal,current)=>{
  state.eventsLoading=true;render();
  try{
   const value=await api.events(query,signal);if(!current())return;
   acceptEvents(value,query);state.errors.events='';recordRead();
   const last=pageWindow(state.eventsMeta?.total??state.events.length,paging.activity.page,paging.activity.pageSize).page;
   if(state.eventsMeta&&last!==paging.activity.page){paging.activity.page=last;return loadEvents()}
  }catch(error){if(current()&&error.name!=='AbortError')state.errors.events=safeMessage(error)}
  finally{if(current()){state.eventsLoading=false;render()}}
 });
}

function recordRead(pageCount,updatedAt=new Date().toISOString()){
 syncLabel.textContent='Cập nhật '+dateTime(updatedAt,state.timezone);
 if(Number.isSafeInteger(pageCount)&&pageCount>=0)document.querySelector('#nav-page-count').textContent=number(pageCount);
}

function loadOverview(){
 const timezone=state.timezone;
 return readView('overview',{timezone},async(signal,current)=>{
  try{
   const value=await api.overview(timezone,signal);if(!current())return;
   state.overview=value;state.errors.overview='';recordRead(value.stats?.pages,value.updated_at);
  }catch(error){if(current()&&error.name!=='AbortError')state.errors.overview=safeMessage(error)}
  finally{if(current())render()}
 });
}

function loadVisible({explicit=false,automatic=false}={}){
 if(!state.ready)return;
 if(state.view==='pages'){
  const pages=loadPages();
  return automatic?pages:Promise.all([pages,loadConnectionOptions({refresh:explicit})]);
 }
 if(state.view!=='settings')return;
 if(state.settingsSection==='overview')return loadOverview();
 if(state.settingsSection==='connections')return loadConnections();
 if(state.settingsSection==='activity')return loadEvents();
 if(explicit&&state.settingsSection==='videosource')return videoSource.refresh();
 if(explicit&&state.settingsSection==='ideapipeline')return ideaPipeline.refresh();
}

function reloadConnectionViews(){
 for(const key of ['connections','options','pages','events','overview'])cancelViewRead(key);
 state.optionsFor=null;state.overview=null;
 return loadVisible();
}

function reloadPageViews(){
 for(const key of ['pages','overview'])cancelViewRead(key);
 return loadVisible();
}

function pageQuery(){return {connection_id:state.connection,search:state.search,information_status:state.informationStatus,today:state.today,timezone:state.timezone,limit:paging.pages.pageSize,offset:(paging.pages.page-1)*paging.pages.pageSize}}
function connectionQuery(){return {limit:paging.connections.pageSize,offset:(paging.connections.page-1)*paging.connections.pageSize}}

function loadPages(){
 const query=pageQuery(),signature=JSON.stringify(query);
 return readView('pages',query,async(signal,current)=>{
  const active=document.activeElement;
  const matches=()=>current()&&signature===JSON.stringify(pageQuery());
  if(state.pagesFor!==signature||state.errors.pages)document.querySelector('#page-rows')?.replaceChildren(skeleton(4));
  try{
   const value=await api.pages(query,signal);if(!matches())return;
   state.pages=canonicalPages(value.items);state.pagesMeta=listMeta(value);state.pagesFor=signature;state.errors.pages='';
   const unfiltered=!query.connection_id&&!query.search&&!query.information_status&&!query.today;
   recordRead(unfiltered?(state.pagesMeta?.total??state.pages.length):undefined);
   if(state.pagesMeta){
    const last=pageWindow(state.pagesMeta.total,paging.pages.page,paging.pages.pageSize).page;
    if(last!==paging.pages.page){paging.pages.page=last;return loadPages()}
   }
   renderPageRows();if(active?.isConnected&&document.activeElement===document.body)active.focus();
  }catch(error){
   if(!matches()||error.name==='AbortError')return;
   state.errors.pages=safeMessage(error);
   document.querySelector('#page-rows')?.replaceChildren(errorBox(state.errors.pages,()=>void loadPages()));
  }
 });
}

function loadConnections(){
 const query=connectionQuery();
 return readView('connections',query,async(signal,current)=>{
  state.connectionsLoading=true;render();
  try{
   const value=await api.connections(query,signal);if(!current())return;
   state.connections=value.items||[];state.connectionsMeta=listMeta(value);state.errors.connections='';recordRead();
   const last=pageWindow(state.connectionsMeta?.total??state.connections.length,paging.connections.page,paging.connections.pageSize).page;
   if(state.connectionsMeta&&last!==paging.connections.page){paging.connections.page=last;return loadConnections()}
  }catch(error){if(current()&&error.name!=='AbortError')state.errors.connections=safeMessage(error)}
  finally{if(current()){state.connectionsLoading=false;render()}}
 });
}

async function readConnectionOptions(query,signal){
 try{return await api.connectionOptions(query,signal)}catch(error){
  if(error.status!==404)throw error;
  const value=await api.connections(query,signal);if(listMeta(value))return value;
  const rows=(value.items||[]).filter(row=>(row.label+' '+row.id).toLocaleLowerCase().includes(query.search.trim().toLocaleLowerCase()));
  return {items:rows.slice(query.offset,query.offset+query.limit),total:rows.length,offset:query.offset,limit:query.limit};
 }
}
function loadConnectionOptions({refresh=false}={}){
 const query={limit:100,offset:state.optionsOffset,search:state.optionsSearch},signature=JSON.stringify(query);
 if(!refresh&&state.optionsFor===signature)return;
 return readView('options',query,async(signal,current)=>{
  const matches=()=>current()&&query.search===state.optionsSearch&&query.offset===state.optionsOffset;
  try{
   const value=await readConnectionOptions(query,signal);if(!matches())return;
   state.connectionOptions=(value.items||[]).map(({id,label,status,connected,has_token})=>({id,label,status,connected,has_token}));
   state.optionsMeta=listMeta(value);state.optionsFor=signature;state.optionsError='';renderSourceOptions();
  }catch(error){if(matches()&&error.name!=='AbortError'){state.optionsError=safeMessage(error);renderSourceOptions()}}
 });
}
function renderSourceOptions(){
 const source=document.querySelector('#page-connection-options'),pager=document.querySelector('#page-connection-pager'),search=document.querySelector('[aria-label="Tìm nguồn token"]');if(!source||!pager)return;
 const selected=source.selectedOptions[0]?.textContent;source.replaceChildren(el('option',{value:'',text:'Tất cả kết nối'}));for(const row of state.connectionOptions)source.append(el('option',{value:row.id,text:row.label}));
 if(state.connection&&!state.connectionOptions.some(row=>String(row.id)===state.connection))source.append(el('option',{value:state.connection,text:selected||'Nguồn #'+state.connection}));source.value=state.connection;
 if(search)search.hidden=!state.optionsSearch&&(state.optionsMeta?.total||0)<=100;
 pager.replaceChildren();if(state.optionsError)pager.append(el('small',{class:'field-hint',text:state.optionsError}));
 if((state.optionsMeta?.total||0)>100||state.optionsOffset){const meta=state.optionsMeta;pager.append(button('Trước',()=>{state.optionsOffset=Math.max(0,state.optionsOffset-100);void loadConnectionOptions()},{small:true,style:'ghost',disabled:!state.optionsOffset,'aria-label':'Nguồn token trước'}),el('small',{text:(state.optionsOffset+1)+'–'+Math.min(state.optionsOffset+100,meta?.total||0)+' / '+(meta?.total||0)}),button('Sau',()=>{state.optionsOffset+=100;void loadConnectionOptions()},{small:true,style:'ghost',disabled:state.optionsOffset+100>=(meta?.total||0),'aria-label':'Nguồn token sau'}))}
}

async function connectionAction(connection,action){
  const key='connection:'+connection.id;if(busy(key))return;state.busy.add(key);render();
  try{
    const value=action==='check'?await api.checkConnection(connection.id):await api.syncConnection(connection.id);
    if(action==='sync')toast(value.complete?'Đã đồng bộ '+number(value.pages_seen)+' Page.':'Đồng bộ một phần: đã đọc '+number(value.pages_seen)+' Page. Các liên kết cũ được giữ.');
    else toast(value.connection?.status==='working'?'Kết nối hợp lệ.':'Đã kiểm tra. Xem tình trạng và quyền của kết nối.');
    await reloadConnectionViews();
  }catch(error){toast(safeMessage(error),'error')}
  finally{state.busy.delete(key);render()}
}

function showConnectionForm(connection=null){
  if(connectionDialog.open)return;
  const title=connection?'Cập nhật kết nối':'Thêm kết nối token';
  const close=button('',()=>connectionDialog.close(),{style:'ghost','aria-label':'Đóng kết nối'});close.replaceChildren(icon('x',19));
  const heading=el('div',{class:'dialog-heading'},el('div',{},el('h2',{id:'connection-dialog-title',text:title}),el('p',{text:'Nguồn truy cập cho các Page được cấp quyền.'})),close);
  const label=el('input',{name:'label',required:true,maxlength:160,value:connection?.label||'',placeholder:'Ví dụ: Tài khoản chính / BM thương hiệu'});
  const type=el('select',{name:'token_type'},Object.entries(TYPE).map(([value,text])=>el('option',{value,text})));type.value=connection?.token_type||'user';
  const token=el('input',{name:'token',type:'password',required:!connection,autocomplete:'new-password',spellcheck:'false',maxlength:16384,placeholder:connection?'Để trống nếu giữ token hiện tại':'Dán access token của bạn'});
  const appId=el('input',{name:'app_id',inputmode:'numeric',pattern:'[0-9]*',maxlength:40,value:connection?.app_id||'',placeholder:'Tùy chọn'});
  const businessId=el('input',{name:'business_id',inputmode:'numeric',pattern:'[0-9]*',maxlength:40,value:connection?.business_id||'',placeholder:'Tùy chọn'});
  const error=el('div',{id:'connection-form-error'}),save=el('button',{type:'submit',class:'button primary',text:connection?'Lưu cập nhật':'Lưu kết nối'});
  const form=el('form',{class:'dialog-form'},el('label',{},'Tên kết nối / ghi chú BM',label),el('label',{},'Loại token',type),el('label',{},connection?'Token thay thế':'Access token',token,el('small',{class:'field-hint',text:'Chỉ nhận token người dùng, system user hoặc Page; không dùng APP_ID|APP_SECRET.'})),el('div',{class:'form-row'},el('label',{},'App ID',appId),el('label',{},'Business ID',businessId)),el('small',{class:'field-hint',text:'Với token User, nhập Business ID để chỉ đồng bộ Page thuộc BM đó mà token truy cập được. Để trống để đồng bộ mọi Page token được cấp quyền.'}),el('div',{class:'private-note'},icon('shield',15),el('span',{text:'Token không được hiển thị lại, ghi vào URL hoặc lưu lâu dài ở trình duyệt. Ứng dụng này độc lập với Chrome Manager.'})),error,el('div',{class:'dialog-actions'},button('Hủy',()=>connectionDialog.close()),save));
  form.addEventListener('submit',async event=>{
    event.preventDefault();error.replaceChildren();
    if(token.value.includes('|')){error.append(errorBox('Token ứng dụng APP_ID|APP_SECRET không được hỗ trợ. Dùng token người dùng, system user hoặc Page.'));return}
    const body={label:label.value.trim(),token_type:type.value,...(connection?{revision:connection.revision}:{}),...(token.value.trim()?{token:token.value.trim()}:{}),...(appId.value.trim()?{app_id:appId.value.trim()}:{}),...(businessId.value.trim()?{business_id:businessId.value.trim()}:{})};
    save.disabled=true;close.disabled=true;save.textContent='Đang lưu…';
    try{if(connection)await api.updateConnection(connection.id,body);else await api.addConnection(body);token.value='';connectionDialog.close();toast('Đã lưu kết nối. Kiểm tra token rồi đồng bộ để đọc Page.');await reloadConnectionViews()}
    catch(reason){
      error.append(errorBox(safeMessage(reason)));close.disabled=false;save.textContent=connection?'Lưu cập nhật':'Lưu kết nối';
      if(reason?.code==='connection_revision_changed'){
        token.value='';save.disabled=true;
        error.append(button('Đóng và đọc lại kết nối',async()=>{connectionDialog.close();await reloadConnectionViews()}));
      }else save.disabled=false;
    }
    finally{delete body.token}
  });
  connectionDialog.replaceChildren(heading,form);
  connectionDialog.addEventListener('close',()=>{token.value='';connectionDialog.replaceChildren()},{once:true});openDialog(connectionDialog);label.focus();
}

function confirmRemoval(connection){
  const cancel=button('Giữ kết nối',()=>confirmDialog.close()),remove=button('Gỡ kết nối',async()=>{
    remove.disabled=true;cancel.disabled=true;
    try{await api.removeConnection(connection.id,connection.revision);confirmDialog.close();toast('Đã ngắt kết nối và xóa token của nguồn này.');await reloadConnectionViews()}
    catch(error){toast(safeMessage(error),'error');remove.disabled=false;cancel.disabled=false}
  },{style:'danger',iconName:'logout'});
  confirmDialog.replaceChildren(el('div',{class:'dialog-heading'},el('div',{},el('h2',{id:'confirm-dialog-title',text:'Gỡ kết nối này?'}),el('p',{text:connection.label}))),el('div',{class:'dialog-form'},el('p',{class:'muted',text:'Token và token Page phát sinh từ nguồn này sẽ được xóa. Lịch sử Page và các kết nối khác được giữ. Không thay đổi quyền hoặc nội dung trên Meta.'}),el('div',{class:'dialog-actions'},cancel,remove)));openDialog(confirmDialog);cancel.focus();
}

async function openPage(id){
  if(busy('page:'+id))return;
  detailController?.abort();detailController=new AbortController();const sequence=++detailSequence;
  state.drawer={id:String(id),loading:true,error:'',data:null};renderDrawer();if(!pageDialog.open)openDialog(pageDialog);
  const setting=postPaging.get(String(id))||{page:1,pageSize:10};postPaging.set(String(id),setting);
  try{const data=await api.detail(id,state.timezone,detailController.signal,{limit:setting.pageSize,offset:(setting.page-1)*setting.pageSize});if(sequence!==detailSequence||state.drawer?.id!==String(id))return;if(Number.isSafeInteger(data.posts_total)){const last=pageWindow(data.posts_total,setting.page,setting.pageSize).page;if(last!==setting.page){setting.page=last;return void openPage(id)}}state.drawer={id:String(id),loading:false,error:'',data};renderDrawer()}
  catch(error){if(error.name==='AbortError'||sequence!==detailSequence)return;state.drawer={id:String(id),loading:false,error:safeMessage(error),data:null};renderDrawer()}
}

function drawerHeading(title,description){const close=button('',()=>pageDialog.close(),{style:'ghost','aria-label':'Đóng chi tiết Page'});close.replaceChildren(icon('x',20));return el('div',{class:'dialog-heading'},el('div',{},el('h2',{id:'page-dialog-title',text:title}),el('p',{text:description})),close)}
function renderDrawer(){
  const current=state.drawer;if(!current)return;
  pageDialog.replaceChildren(drawerHeading('Chi tiết Page','Thông tin và bài viết đã lưu cho đúng Meta Page ID.'));
  const body=el('div',{class:'drawer-body'});pageDialog.append(body);
  if(current.loading){body.append(skeleton(6));return}
  if(current.error){body.append(errorBox(current.error,()=>void openPage(current.id)));return}
  const data=current.data,page=data.page,today=countToday(page);
  body.append(el('div',{class:'drawer-identity'},avatar(page.name,page.id,page.avatar_url),el('div',{},el('h3',{text:page.name}),el('small',{text:'Meta Page ID · '+page.id}))));
  const publicUrl=facebookUrl(page.link)||(/^\d+$/.test(String(page.id))?'https://www.facebook.com/'+page.id:null);
  const actions=el('div',{class:'drawer-status'},statusBadge(page.status),badge(today.unknown?today.label:today.label+' bài hôm nay',today.unknown?'neutral':'success'));
  if(publicUrl)actions.append(el('a',{class:'button small',href:publicUrl,target:'_blank',rel:'noopener noreferrer'},icon('external',13),'Mở Page'));
  actions.append(button('Sao chép ID',()=>{void navigator.clipboard?.writeText(String(page.id)).then(()=>toast('Đã sao chép Meta Page ID.')).catch(()=>toast('Không sao chép được ID. Bạn có thể chọn ID hiển thị.','error'))},{small:true,iconName:'copy'}));body.append(actions);
  actions.append(button('Chỉnh sửa Page',()=>showPageEditor({page,connections:connectionsOf(page),initialConnectionId:state.sources.get(String(page.id))||'',onRefresh:({pageId,connectionId})=>{state.sources.set(String(pageId),String(connectionId));return refreshDetail(pageId)},onChanged:()=>void reloadPageViews()}),{small:true,iconName:'settings',disabled:state.info.page_editing!==true||busy('page:'+page.id)||!connectionsOf(page).some(value=>value.status!=='disconnected'),title:state.info.page_editing===true?'Chọn nguồn và xem lại thay đổi trước khi lưu':'Mở lại server để dùng chỉnh sửa Page'}));
  if(page.error)body.append(el('p',{class:'inline-alert',text:page.error}));
  const details=page.details||{},grid=el('dl',{class:'detail-grid'});
  for(const [title,value] of [['Danh mục',page.category],['Người theo dõi',details.followers_count===undefined?null:number(details.followers_count)],['Website',details.website],['Giờ mở cửa',typeof details.is_always_open==='boolean'?details.is_always_open?'Luôn mở cửa':'Không bật luôn mở cửa':null],['Điện thoại',details.phone],['Giới thiệu',details.about],['Địa chỉ',typeof details.location==='string'?details.location:details.location?[details.location.street,details.location.city,details.location.state,details.location.country,details.location.zip].filter(Boolean).join(', '):null]])grid.append(el('div',{},el('dt',{text:title}),el('dd',{text:value||'Chưa có thông tin'})));
  body.append(grid);
  const source=el('select',{'aria-label':'Nguồn đọc Page',disabled:busy('page:'+page.id)},el('option',{value:'',text:'Tự chọn nguồn đang khả dụng'}));
  for(const connection of connectionsOf(page))source.append(el('option',{value:connection.id,disabled:connection.status==='disconnected',text:connection.label+' · '+(STATUS[connection.status]?.[0]||'Chưa kiểm tra')}));
  source.value=state.sources.get(String(page.id))||'';
  if(source.value!==String(state.sources.get(String(page.id))||''))state.sources.delete(String(page.id));
  source.addEventListener('change',()=>state.sources.set(String(page.id),source.value));
  body.append(el('section',{class:'drawer-source'},el('label',{},'Nguồn để cập nhật',source),el('div',{class:'source-row'},button(busy('page:'+page.id)?'Đang đọc Meta…':'Cập nhật thông tin & bài viết',()=>void refreshDetail(page.id),{iconName:'refresh',style:'primary',disabled:busy('page:'+page.id)||!connectionsOf(page).some(item=>item.status!=='disconnected')})),el('p',{class:'field-hint',text:'Chỉ chọn nguồn được liên kết với Page này. Đổi nguồn không tự gọi Meta; bấm cập nhật khi sẵn sàng.'})));
  body.append(el('div',{class:'post-heading'},el('h3',{text:'Bài viết gần đây'}),el('span',{class:'cache-tag'},icon('clock',12),data.posts_checked_at?relative(data.posts_checked_at):'Chưa đọc bài')));
  if(data.posts_error)body.append(el('p',{class:'inline-alert',text:data.posts_error}));
  const posts=[...new Map((data.posts||[]).map(post=>[String(post.id),post])).values()],postSetting=postPaging.get(current.id)||{page:1,pageSize:10};postPaging.set(current.id,postSetting);
  const serverPosts=Number.isSafeInteger(data.posts_total)&&Number.isSafeInteger(data.posts_offset),postView=pageWindow(serverPosts?data.posts_total:posts.length,postSetting.page,postSetting.pageSize);postSetting.page=postView.page;
  const postOffset=serverPosts?data.posts_offset:0,visiblePosts=posts.slice(postView.start-postOffset,postView.end-postOffset);
  if(!posts.length)body.append(empty('Chưa có bài viết trong cache',data.posts_checked_at?'Chưa đọc được bài viết trong phạm vi đã kiểm tra.':'Chọn nguồn và cập nhật để đọc bài viết đã xuất bản.'));
  else for(const post of visiblePosts){
    const card=el('article',{class:'post-card'},el('time',{datetime:post.created_time||''},icon('clock',12),dateTime(post.created_time,state.timezone)));
    const message=post.message||'Bài viết không có nội dung văn bản.';
    if(message.length>650)card.append(el('p',{text:message.slice(0,650)+'…'}),el('details',{},el('summary',{text:'Đọc đầy đủ'}),el('p',{text:message})));
    else card.append(el('p',{text:message}));
    const link=facebookUrl(post.permalink_url);if(link)card.append(el('a',{class:'post-link',href:link,target:'_blank',rel:'noopener noreferrer'},'Xem bài trên Facebook',icon('external',12)));body.append(card);
  }
  if(posts.length)body.append(createPagination({label:'Bài viết đã lưu',...postView,onPageChange:page=>{if(busy('page:'+current.id))return;postSetting.page=page;serverPosts?void openPage(current.id):renderDrawer()},onPageSizeChange:pageSize=>{if(busy('page:'+current.id))return;postSetting.pageSize=pageSize;postSetting.page=1;serverPosts?void openPage(current.id):renderDrawer()}}));
  if(data.posts_has_more)body.append(el('p',{class:'inline-alert',text:'Đây là phần bài viết đã đọc, có thể còn bài khác. Số bài hôm nay chưa được coi là đầy đủ.'}));
  body.append(el('p',{class:'field-hint',text:'Thông tin cập nhật: '+dateTime(page.checked_at,state.timezone)+' · Bài viết: '+dateTime(data.posts_checked_at,state.timezone)}));
}

async function refreshDetail(id){
  if(busy('page:'+id)||state.drawer?.id!==String(id))return;
  const source=state.sources.get(String(id))||'';const sequence=++detailSequence;detailController?.abort();detailController=new AbortController();state.busy.add('page:'+id);renderDrawer();
  try{const data=await api.refreshPage(id,source,state.timezone,detailController.signal);if(sequence!==detailSequence||state.drawer?.id!==String(id))return;if(postPaging.has(String(id)))postPaging.get(String(id)).page=1;state.drawer={id:String(id),loading:false,error:'',data};toast(data.posts_error||data.page.error?'Đã cập nhật phần dữ liệu có quyền đọc; xem ghi chú trong Page.':'Đã cập nhật thông tin và bài viết của Page.');void reloadPageViews()}
  catch(error){if(error.name==='AbortError'||sequence!==detailSequence)return;toast(safeMessage(error),'error')}
  finally{state.busy.delete('page:'+id);if(sequence===detailSequence&&state.drawer?.id===String(id))renderDrawer()}
}

pageDialog.addEventListener('close',()=>{detailSequence++;detailController?.abort();state.drawer=null;pageDialog.replaceChildren()});
window.addEventListener('popstate',()=>navigateLocation({historyMode:'replace'}));
shell();navigateLocation({load:false});
try{
  state.info=await initialize();state.timezone=state.info.timezone||state.timezone;state.ready=true;state.loading=false;
  document.querySelector('#app-version').textContent='v'+(state.info.version||'1.0');
  syncLabel.textContent='Đã kết nối';navigateLocation({historyMode:'replace',load:false});
  await loadVisible();announce('Đã tải không gian Page Manager.');
}catch(error){state.loading=false;content.replaceChildren(heading(),errorBox(safeMessage(error),()=>location.reload()),footer());syncLabel.textContent='Chưa kết nối'}
setInterval(()=>{if(document.visibilityState==='visible'&&!document.querySelector('dialog[open]')&&!state.busy.size)void loadVisible({automatic:true})},60000);
