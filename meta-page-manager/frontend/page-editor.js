import {api} from './api.js';
import {el,icon,button,errorBox,skeleton,openDialog,safeMessage} from './ui.js';

const ADDRESS=[['street','Đường / số nhà'],['city','Thành phố'],['state','Bang / tỉnh'],['country','Quốc gia'],['zip','Mã bưu chính']];
const FIELD_NAMES={bio:'Bio / Giới thiệu ngắn',location:'Địa chỉ',category_ids:'Danh mục',website:'Website',is_always_open:'Giờ mở cửa',avatar:'Ảnh đại diện',cover:'Ảnh bìa'};
const allowedImageTypes=['image/jpeg','image/png'];
const text=value=>typeof value==='string'?value:'';
const numericId=value=>/^\d{1,30}$/.test(String(value||''));
const unknownCurrent='Chưa đọc được dữ liệu hiện tại';
const hoursText=value=>value?'Luôn mở cửa':'Không bật luôn mở cửa';
function validWebsite(value){try{const url=new URL(value);return !/[\s\x00-\x1f\x7f]/.test(value)&&url.protocol==='https:'&&!!url.hostname&&!url.username&&!url.password&&(!url.port||url.port==='443')}catch{return false}}
function address(value){return Object.fromEntries(ADDRESS.map(([key])=>[key,text(value?.[key])]))}
function categoryList(current){const names=new Map((current.category_list||[]).filter(value=>numericId(value.id)).map(value=>[String(value.id),text(value.name)]));return (current.category_ids||[]).filter(numericId).map(id=>({id:String(id),name:names.get(String(id))||'ID '+id}))}

export function showPageEditor({page,connections,initialConnectionId='',onRefresh,onChanged}){
 const dialog=document.querySelector('#page-edit-dialog');
 if(!dialog||dialog.open)return;
 const pageId=String(page.id),pageName=text(page.name)||'Page '+pageId,linked=connections.filter(value=>value.status!=='disconnected');
 let connectionId=linked.some(value=>String(value.id)===String(initialConnectionId))?String(initialConnectionId):'';
 let mode='edit',options=null,loading=false,error='',draft=null,review=null,saving=false,generation=0,controller=null,categoryTimer=null,categoryGeneration=0,categoryController=null;
 let categoryResults=[],categoryLoading=false,categoryError='',categoryQuery='',categoryPartial=false;
 const urls=new Map();
 const supports=key=>options?.supported_fields?.includes(key);
 function revoke(){for(const value of urls.values())URL.revokeObjectURL(value);urls.clear()}
 function resetDraft(){revoke();review=null;categoryController?.abort();clearTimeout(categoryTimer);categoryLoading=false;categoryResults=[];categoryQuery='';categoryError='';categoryPartial=false;categoryGeneration++;draft=options?{enabled:{bio:false,location:false,category_ids:false,website:false,is_always_open:false},bio:text(options.current?.bio),location:address(options.current?.location),categories:categoryList(options.current||{}),website:text(options.current?.website),is_always_open:typeof options.current?.is_always_open==='boolean'?options.current.is_always_open:null,avatar:null,cover:null}:null}
 function cleanup(){generation++;categoryGeneration++;controller?.abort();categoryController?.abort();clearTimeout(categoryTimer);revoke();dialog.replaceChildren()}
 function cancel(event){if(saving)event.preventDefault()}
 dialog.addEventListener('cancel',cancel);
 dialog.addEventListener('close',()=>{dialog.removeEventListener('cancel',cancel);cleanup()},{once:true});

 async function loadOptions(){
  const sequence=++generation,selected=connectionId;controller?.abort();controller=new AbortController();options=null;resetDraft();loading=true;error='';render();
  try{
   const value=await api.pageEditOptions(pageId,selected,controller.signal);
   if(sequence!==generation||selected!==connectionId||!dialog.open)return;
   if(String(value.page_id)!==pageId||String(value.connection_id)!==selected||!Array.isArray(value.supported_fields)||!value.current)throw new Error('Invalid edit binding');
   options=value;resetDraft();
  }catch(reason){if(sequence!==generation||reason.name==='AbortError')return;error=reason.name==='ApiError'?safeMessage(reason):'Chưa xác nhận được đúng Page và nguồn chỉnh sửa. Đọc lại trước khi tiếp tục.'}
  finally{if(sequence===generation){loading=false;render()}}
 }

 function heading(){return el('div',{class:'dialog-heading'},el('div',{},el('h2',{id:'page-edit-title',text:mode==='review'?'Xem lại thay đổi':mode==='result'?'Kết quả gửi thay đổi':'Chỉnh sửa Page'}),el('p',{text:pageName+' · Meta Page ID '+pageId})),button('',()=>dialog.close(),{style:'ghost',disabled:saving,'aria-label':'Đóng chỉnh sửa Page',title:'Đóng chỉnh sửa Page'}))}
 function render(){
  const title=heading();title.lastElementChild.replaceChildren(icon('x',20));dialog.replaceChildren(title);
  const body=el('div',{class:'page-editor-body'});dialog.append(body);
  if(mode==='review'||mode==='result'){renderReview(body);return}
  const source=el('select',{'aria-label':'Nguồn chỉnh sửa Page',disabled:loading,onChange:event=>{connectionId=event.target.value;error='';options=null;resetDraft();if(connectionId)void loadOptions();else render()}},el('option',{value:'',text:'Chọn kết nối cho Page này'}));
  for(const value of linked)source.append(el('option',{value:value.id,text:value.label}));source.value=connectionId;
  body.append(el('div',{class:'page-editor-binding'},el('strong',{text:'Đúng Page · Đúng nguồn'}),el('p',{text:'Thay đổi chỉ gửi đến Meta sau bước xem lại và bấm Lưu lên Page.'}),el('label',{},'Kết nối được dùng để chỉnh sửa',source),el('small',{class:'field-hint',text:'Đổi kết nối sẽ xóa bản nháp. Chỉ hiển thị nguồn đã liên kết với Page này.'})));
  if(loading){body.append(skeleton(4));return}
  if(error)body.append(errorBox(error,connectionId?()=>void loadOptions():undefined));
  if(!options||!draft){if(!error)body.append(el('p',{class:'field-hint',text:linked.length?'Chọn nguồn để đọc khả năng chỉnh sửa và thông tin hiện tại.':'Page chưa có kết nối đang hoạt động để chỉnh sửa.'}));return}
  body.append(el('p',{class:'page-editor-note',text:'Chỉ mục được đánh dấu hoặc ảnh bạn chọn mới được gửi. Dữ liệu trong danh sách Page chưa đổi khi đang soạn bản nháp.'}));
  const bio=el('textarea',{rows:3,maxlength:options.limits?.bio_max_length||101,disabled:!draft.enabled.bio,value:draft.bio,'aria-label':'Bio mới',onInput:event=>{draft.bio=event.target.value}});
  body.append(fieldSection('bio',el('label',{},'Nội dung bio / giới thiệu ngắn',bio,options.current?.bio_field==='about'?el('small',{class:'field-hint',text:'Đang sửa phần Giới thiệu ngắn của Page.'}):null,el('small',{class:'field-hint',text:'Tối đa '+(options.limits?.bio_max_length||101)+' ký tự. Có thể để trống để xóa nội dung hiện tại.'}))));
  const location=el('div',{class:'page-editor-fields'});
  for(const [key,label] of ADDRESS)location.append(el('label',{},label,el('input',{value:draft.location[key],maxlength:200,disabled:!draft.enabled.location,'aria-label':'Địa chỉ · '+label,onInput:event=>{draft.location[key]=event.target.value}})));
  body.append(fieldSection('location',location));
  body.append(fieldSection('category_ids',renderCategories()));
  if(supports('website'))body.append(fieldSection('website',el('label',{},'Địa chỉ website',el('input',{type:'url',value:draft.website,maxlength:2048,disabled:!draft.enabled.website,'aria-label':'Website mới',placeholder:'https://example.com',onInput:event=>{draft.website=event.target.value}}),el('small',{class:'field-hint',text:'Dùng địa chỉ HTTPS. Để trống để xóa website đã đọc được; dữ liệu chưa rõ sẽ không tự bị xóa.'}))));
  if(supports('is_always_open')){
   const hours=el('input',{type:'checkbox',checked:draft.is_always_open===true,disabled:!draft.enabled.is_always_open,'aria-label':'Luôn mở cửa',onChange:event=>{draft.is_always_open=event.target.checked}});
   hours.indeterminate=draft.is_always_open===null;
   body.append(fieldSection('is_always_open',el('div',{},el('label',{class:'page-editor-toggle'},hours,el('span',{text:'Luôn mở cửa'})),el('small',{class:'field-hint',text:typeof options.current?.is_always_open==='boolean'?'Hiện tại: '+hoursText(options.current.is_always_open)+'. Bỏ chọn là yêu cầu tắt chế độ luôn mở cửa, không có nghĩa Page đóng cửa.':'Chưa đọc được trạng thái hiện tại. Chọn ô để yêu cầu luôn mở cửa; chọn rồi bỏ chọn để yêu cầu tắt chế độ này.'}))));
  }
  body.append(el('div',{class:'page-editor-images'},imageField('avatar'),imageField('cover')));
  body.append(el('div',{class:'dialog-actions'},button('Hủy',()=>dialog.close()),button('Xem lại thay đổi',()=>makeReview(),{style:'primary',iconName:'eye'})));
 }
 function fieldSection(key,fields){
  const supported=supports(key),checked=!!draft.enabled[key];
  return el('section',{class:'page-editor-section'},el('label',{class:'page-editor-toggle'},el('input',{type:'checkbox',checked,disabled:!supported,'aria-label':'Thay đổi '+FIELD_NAMES[key],onChange:event=>{draft.enabled[key]=event.target.checked;error='';render()}}),el('span',{text:'Thay đổi '+FIELD_NAMES[key]})),supported?fields:el('p',{class:'field-hint',text:'Nguồn này chưa hỗ trợ chỉnh sửa mục này qua API.'}));
 }
 function imageField(kind){
  const chosen=draft[kind],limit=options.limits?.image_max_bytes||8388608;
  const input=el('input',{type:'file',accept:'image/jpeg,image/png',disabled:!supports(kind),'aria-label':'Chọn '+FIELD_NAMES[kind].toLowerCase(),onChange:event=>{
   const file=event.target.files?.[0];if(!file)return;
   if(!allowedImageTypes.includes(file.type)||file.size>limit||file.size===0){error='Chỉ chọn ảnh JPEG hoặc PNG không quá '+Math.floor(limit/1048576)+' MB.';render();return}
   if(urls.has(kind))URL.revokeObjectURL(urls.get(kind));urls.set(kind,URL.createObjectURL(file));draft[kind]=file;error='';render();
  }});
  const field=el('section',{class:'page-editor-section page-editor-image'},el('h3',{text:FIELD_NAMES[kind]}),el('small',{class:'field-hint',text:'JPEG / PNG · Tối đa '+Math.floor(limit/1048576)+' MB · Chưa gửi lên Meta'}));
  if(chosen)field.append(el('img',{class:'page-editor-preview '+kind,src:urls.get(kind),alt:'Xem trước '+FIELD_NAMES[kind].toLowerCase()}),el('p',{class:'page-editor-file',text:chosen.name}),button('Bỏ ảnh đã chọn',()=>{URL.revokeObjectURL(urls.get(kind));urls.delete(kind);draft[kind]=null;render()},{small:true,style:'ghost','aria-label':'Bỏ '+FIELD_NAMES[kind].toLowerCase()}));
  field.append(supports(kind)?input:el('p',{class:'field-hint',text:'Nguồn này chưa hỗ trợ tải ảnh cho Page.'}));return field;
 }
 function renderCategories(){
  const enabled=draft.enabled.category_ids,box=el('div',{}),selected=el('div',{class:'page-editor-category-chips'});
  for(const category of draft.categories)selected.append(el('span',{class:'page-editor-category'},el('span',{text:category.name}),el('small',{text:'ID '+category.id}),enabled?button('',()=>{draft.categories=draft.categories.filter(value=>value.id!==category.id);render()},{small:true,style:'ghost','aria-label':'Bỏ danh mục '+category.name}):null));
  for(const remove of selected.querySelectorAll('button'))remove.replaceChildren(icon('x',13));
  box.append(selected);
  if(!enabled)return box;
  const search=el('input',{type:'search',value:categoryQuery,'aria-label':'Tìm danh mục Page',placeholder:'Nhập tên danh mục…',onInput:event=>{categoryQuery=event.target.value;categoryGeneration++;categoryController?.abort();categoryResults=[];categoryError='';categoryLoading=false;categoryPartial=false;renderCategoryResults();clearTimeout(categoryTimer);categoryTimer=setTimeout(()=>void searchCategories(),300)}});
  box.append(el('label',{},'Tìm danh mục',search),el('p',{class:'field-hint',text:'Chọn tối đa '+(options.limits?.category_max_count||3)+' danh mục từ kết quả API. Không suy đoán ID từ tên hiển thị.'}));
  const results=el('div',{class:'page-editor-category-results',id:'page-editor-category-results'});box.append(results);renderCategoryResults(results);return box;
 }
 function renderCategoryResults(target=document.querySelector('#page-editor-category-results')){
  if(!target)return;target.replaceChildren();
  if(categoryLoading){target.append(el('p',{class:'field-hint',role:'status',text:'Đang tìm danh mục…'}));return}
  if(categoryError){target.append(el('p',{class:'inline-alert',text:categoryError}));return}
  if(categoryPartial)target.append(el('p',{class:'field-hint',text:'API mới trả về một phần danh mục. Các kết quả dưới đây vẫn dùng đúng ID được API cung cấp.'}));
  for(const value of categoryResults){const selected=draft.categories.some(category=>category.id===value.id);target.append(button(value.name,()=>{if(selected)return;if(draft.categories.length>=(options.limits?.category_max_count||3)){categoryError='Đã đạt số danh mục tối đa.';renderCategoryResults();return}draft.categories.push(value);categoryError='';render()},{disabled:selected,'aria-label':'Chọn danh mục '+value.name}))}
 }
 async function searchCategories(){
  const query=categoryQuery.trim(),selected=connectionId,sequence=++categoryGeneration;
  categoryResults=[];categoryError='';if(query.length<2){categoryLoading=false;renderCategoryResults();return}
  if(!api.pageCategories){categoryError='Tính năng tìm danh mục chưa sẵn sàng trên server này.';renderCategoryResults();return}
  categoryController?.abort();categoryController=new AbortController();categoryLoading=true;renderCategoryResults();
  try{const value=await api.pageCategories(pageId,selected,query,categoryController.signal);if(sequence!==categoryGeneration||selected!==connectionId||query!==categoryQuery.trim()||mode!=='edit'||!draft.enabled.category_ids||!dialog.open)return;categoryPartial=value.complete===false;categoryResults=(value.items||[]).filter(item=>numericId(item.id)&&text(item.name)).map(item=>({id:String(item.id),name:text(item.name)}));if(!categoryResults.length)categoryError='Chưa có danh mục phù hợp trong kết quả API.'}
  catch(reason){if(sequence===categoryGeneration&&reason.name!=='AbortError')categoryError=safeMessage(reason)}
  finally{if(sequence===categoryGeneration){categoryLoading=false;renderCategoryResults()}}
 }
 function makeReview(){
  error='';const current=options.current||{},patch={},changes=[];
  if(draft.enabled.bio&&draft.bio!==text(current.bio)){patch.bio=draft.bio;changes.push({name:current.bio_field==='about'?'Giới thiệu ngắn':FIELD_NAMES.bio,before:typeof current.bio==='string'?current.bio:unknownCurrent,after:draft.bio})}
  if(draft.enabled.location){const before=address(current.location),next=address(draft.location);if(JSON.stringify(before)!==JSON.stringify(next)){patch.location=Object.fromEntries(ADDRESS.filter(([key])=>next[key]||before[key]).map(([key])=>[key,next[key]]));const known=current.location!==null&&typeof current.location==='object'&&!Array.isArray(current.location);changes.push({name:'Địa chỉ',before:known?ADDRESS.map(([key])=>before[key]).filter(Boolean).join(', '):unknownCurrent,after:ADDRESS.map(([key])=>next[key]).filter(Boolean).join(', ')})}}
  if(draft.enabled.category_ids){const previous=categoryList(current),ids=draft.categories.map(value=>value.id);if(JSON.stringify(previous.map(value=>value.id))!==JSON.stringify(ids)){if(!ids.length){error='Chọn ít nhất một danh mục từ kết quả API.';render();return}patch.category_ids=ids;changes.push({name:'Danh mục',before:Array.isArray(current.category_ids)?previous.map(value=>value.name+' (ID '+value.id+')').join(', '):unknownCurrent,after:draft.categories.map(value=>value.name+' (ID '+value.id+')').join(', ')})}}
  if(draft.enabled.website){const next=draft.website.trim();if(next!==text(current.website)){if(next&&!validWebsite(next)){error='Website phải là địa chỉ HTTPS hợp lệ, không kèm thông tin đăng nhập.';render();return}patch.website=next;changes.push({name:FIELD_NAMES.website,before:typeof current.website==='string'?current.website:unknownCurrent,after:next})}}
  if(draft.enabled.is_always_open){if(typeof draft.is_always_open!=='boolean'){error='Chọn rõ trạng thái Luôn mở cửa trước khi xem lại thay đổi.';render();return}if(draft.is_always_open!==current.is_always_open){patch.is_always_open=draft.is_always_open;changes.push({name:FIELD_NAMES.is_always_open,before:typeof current.is_always_open==='boolean'?hoursText(current.is_always_open):unknownCurrent,after:hoursText(draft.is_always_open)})}}
  const operations=[];
  if(Object.keys(patch).length)operations.push({kind:'text',label:'Thông tin Page',requestId:crypto.randomUUID(),payload:{...patch},outcome:null});
  for(const kind of ['avatar','cover'])if(draft[kind]){operations.push({kind,label:FIELD_NAMES[kind],requestId:crypto.randomUUID(),file:draft[kind],preview:urls.get(kind),outcome:null});changes.push({name:FIELD_NAMES[kind],before:'Ảnh hiện tại trên Page',after:draft[kind].name,preview:urls.get(kind),kind})}
  if(!operations.length){error='Chưa có thay đổi để gửi. Chọn mục cần sửa hoặc chọn ảnh mới.';render();return}
  review={pageId,pageName,connectionId:Number(connectionId),connectionLabel:linked.find(value=>String(value.id)===connectionId)?.label||'Kết nối '+connectionId,changes,operations,attempted:false};mode='review';render();dialog.scrollTop=0;
 }
 function renderReview(body){
  body.append(el('div',{class:'page-editor-binding'},el('strong',{text:review.pageName}),el('p',{text:'Meta Page ID '+review.pageId}),el('p',{text:'Nguồn: '+review.connectionLabel+' · #'+review.connectionId})));
  if(mode==='review')body.append(el('p',{class:'page-editor-note',text:'Lưu sẽ thay đổi đúng Page và nguồn bên trên. Các mục không liệt kê sẽ được giữ nguyên. Meta xử lý từng mục; có thể chỉ một phần được tiếp nhận.'}));
  const changes=el('div',{class:'page-editor-review'});
  for(const value of review.changes){const row=el('section',{},el('h3',{text:value.name}),el('dl',{},el('div',{},el('dt',{text:'Trước khi chỉnh sửa'}),el('dd',{text:value.before||'(Trống)'})),el('div',{},el('dt',{text:'Nội dung yêu cầu'}),el('dd',{text:value.after||'(Xóa nội dung)'}))));if(value.preview)row.append(el('img',{class:'page-editor-preview '+value.kind,src:value.preview,alt:'Xem lại '+value.name.toLowerCase()}));changes.append(row)}
  if(mode==='result'){
   const results=el('div',{class:'page-editor-results',role:'status'});
   for(const operation of review.operations){const label=operation.outcome==='accepted'?'Meta đã tiếp nhận':operation.outcome==='partial'?'Chỉ tiếp nhận một phần':operation.outcome==='unknown'?'Chưa xác định kết quả':operation.outcome==='failed'?'Chưa được áp dụng':'Chưa gửi';results.append(el('div',{},el('strong',{text:operation.label+' · '+label}),operation.message?el('p',{text:operation.message}):null))}
   body.append(results,el('p',{class:'inline-alert',text:'API tiếp nhận chưa đồng nghĩa thay đổi đã hiển thị. Đọc lại Page để kiểm tra trước khi tạo lần chỉnh sửa mới. Không tự động gửi lại mục đã thử.'}),el('div',{class:'dialog-actions'},button('Đóng',()=>dialog.close()),button('Đọc lại Page',()=>{const binding={pageId:review.pageId,connectionId:review.connectionId};dialog.close();void onRefresh?.(binding)},{style:'primary',iconName:'refresh'})),el('details',{class:'page-editor-requested'},el('summary',{text:'Xem nội dung đã yêu cầu'}),changes));return;
  }
  body.append(changes);
  body.append(el('div',{class:'dialog-actions'},button('Quay lại chỉnh sửa',()=>{mode='edit';review=null;render()},{disabled:saving}),button(saving?'Đang gửi thay đổi…':'Lưu lên Page',()=>void save(),{style:'primary',disabled:saving||review.attempted,iconName:'check'})));
 }
 async function save(){
  if(saving||!review||review.attempted)return;
  review.attempted=true;saving=true;render();
  for(const operation of review.operations){
   try{
    const result=operation.kind==='text'?await api.editPage(review.pageId,{connection_id:review.connectionId,request_id:operation.requestId,...operation.payload}):await api.editPageImage(review.pageId,review.connectionId,operation.kind,operation.requestId,operation.file);
    operation.outcome=['accepted','partial','unknown','failed'].includes(result.outcome)?result.outcome:'unknown';operation.message=text(result.message)||'Đọc lại Page để kiểm tra kết quả.';
    if(operation.outcome!=='accepted'||result.ok!==true){if(operation.outcome==='accepted')operation.outcome='unknown';break}
   }catch(reason){operation.outcome='unknown';operation.message=safeMessage(reason)+' Không tự gửi lại; hãy đọc lại Page trước.';break}
  }
  saving=false;mode='result';render();dialog.scrollTop=0;onChanged?.();
 }
 render();openDialog(dialog);if(connectionId)void loadOptions();
}
