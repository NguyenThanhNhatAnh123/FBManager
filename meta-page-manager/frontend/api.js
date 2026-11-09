// Same-origin API only. Access tokens are write-only and never browser storage.
const modulePath=new URL(import.meta.url).pathname;
const staticBoundary=modulePath.lastIndexOf('/static/');
export const APP_BASE=staticBoundary<0?'':modulePath.slice(0,staticBoundary);
let csrfToken='';
let appInfo=null;
let sessionRequest=null;
let identityCsrf='';
async function identitySession(){
  const response=await fetch('/auth/api/me',{credentials:'same-origin',cache:'no-store'});
  if(response.status===401){location.assign('/auth/login?next='+encodeURIComponent(location.pathname+location.search));throw new ApiError('Đăng nhập để tiếp tục.',401)}
  if(!response.ok)throw new ApiError('Chưa đọc được tài khoản. Thử tải lại trang.',response.status);
  const value=await response.json();
  if(value.user?.must_change_password){location.assign('/auth/account');throw new ApiError('Đổi mật khẩu ban đầu để tiếp tục.',403)}
  identityCsrf=value.csrf_token;return value.user;
}

export class ApiError extends Error {
  constructor(message,status=0,code=null){super(message);this.name='ApiError';this.status=status;this.code=typeof code==='string'&&/^[a-z][a-z0-9_]{0,63}$/.test(code)?code:null}
}

export async function initialize(force=false){
  if(sessionRequest)return sessionRequest;
  if(!force&&appInfo&&csrfToken)return appInfo;
  sessionRequest=(async()=>{
    await identitySession();
    let response;
    try{response=await fetch(APP_BASE+'/api/session',{credentials:'same-origin',cache:'no-store'})}
    catch{throw new ApiError('Không kết nối được Page Manager. Kiểm tra ứng dụng đang chạy rồi thử lại.')}
    if(!response.ok)throw new ApiError('Chưa khởi tạo được phiên Page Manager.',response.status);
    const value=await response.json();
    if(typeof value.csrf_token!=='string'||!value.csrf_token)throw new ApiError('Server chưa cấp phiên làm việc hợp lệ.');
    csrfToken=value.csrf_token;appInfo=value.app||{};return appInfo;
  })();
  try{return await sessionRequest}finally{sessionRequest=null}
}

async function request(path,{method='GET',body,signal,retried=false,extraHeaders={},versioned=false}={}){
  if(!path.startsWith('/api/')||path.startsWith('//'))throw new ApiError('Địa chỉ API không hợp lệ.');
  if(!csrfToken)await initialize();
  const name=path.slice('/api/'.length).split(/[/?]/,1)[0];
  const resourceAvailable=!['idea-pipeline','telegram-approval'].includes(name)||supportsApiResource(name);
  const useV1=versioned&&resourceAvailable&&(appInfo?.rest_api_version===1||method==='GET'&&appInfo?.read_api_version===1);
  const route=useV1?'/api/v1/'+path.slice('/api/'.length):path;
  const headers={Accept:'application/json',...extraHeaders};
  const binary=body instanceof Blob;
  if(method!=='GET'){headers['X-MPM-CSRF']=csrfToken;headers['X-Via-CSRF']=identityCsrf;if(body!==undefined)headers['Content-Type']=binary?body.type:'application/json'}
  let response;
  try{response=await fetch(APP_BASE+route,{method,headers,credentials:'same-origin',cache:'no-store',signal,...(body!==undefined?{body:binary?body:JSON.stringify(body)}:{})})}
  catch(error){if(error.name==='AbortError')throw error;throw new ApiError('Mất kết nối với Page Manager. Kiểm tra server rồi thử lại.')}
  let value;
  try{value=await response.json()}catch{if(response.status===401)value={};else throw new ApiError('Phản hồi của server chưa hợp lệ. Thử tải lại dữ liệu.',response.status)}
  const expired=response.status===401||response.status===403&&(['session_required','csrf_required','csrf_invalid'].includes(value.code)||!value.code&&!appInfo?.rest_api_resources);
  if(expired&&!retried){
    await initialize(true);
    // Reinitialize once, but never replay a secret write or another mutation.
    if(method==='GET')return request(path,{method,signal,retried:true,versioned});
    throw new ApiError('Phiên làm việc đã được làm mới. Kiểm tra nội dung và bấm lại thao tác nếu cần.',response.status);
  }
  if(!response.ok){
    let message=typeof value.detail==='string'?value.detail:'Chưa hoàn tất thao tác. Kiểm tra kết nối và thử lại.';
    for(const key of ['token','bot_token','password'])if(body?.[key])message=message.split(body[key]).join('[bí mật đã ẩn]');
    throw new ApiError(message.slice(0,600),response.status,value.code);
  }
  return value;
}

const resource=(path,options)=>request(path,{...options,versioned:true});
export function supportsApiResource(name){return appInfo?.rest_api_resources?.includes(name)===true}
export function supportsApiFeature(name){return appInfo?.rest_api_features?.includes(name)===true}
export function resourceUrl(path){
  if(!/^\/api\/(?:pages|reels|idea-pipeline|telegram-approval)(?:\/|$)/.test(path)||/[?#]/.test(path))throw new ApiError('Địa chỉ nội dung không hợp lệ.');
  const name=path.slice('/api/'.length).split('/',1)[0];
  const resourceAvailable=!['idea-pipeline','telegram-approval'].includes(name)||supportsApiResource(name);
  return APP_BASE+(resourceAvailable&&(appInfo?.rest_api_version===1||appInfo?.read_api_version===1)?'/api/v1/'+path.slice('/api/'.length):path);
}
function query(values){const params=new URLSearchParams();for(const [key,value] of Object.entries(values))if(value!==''&&value!==null&&value!==undefined)params.set(key,String(value));return params.size?'?'+params:''}
export async function readPageWindow(fetchPage,params,signal){
  const value=await fetchPage(params,signal);
  if(!Number.isSafeInteger(value?.total)||!Number.isSafeInteger(value?.offset))return value;
  const last=Math.max(0,Math.floor((Math.max(1,value.total)-1)/params.limit)*params.limit);
  return value.offset>last?fetchPage({...params,offset:last},signal):value;
}
export const api={
  overview:(timezone,signal)=>resource('/api/overview'+query({timezone}),{signal}),
  connections:(values={},signal)=>{if(values instanceof AbortSignal){signal=values;values={}}return resource('/api/connections'+query(values),{signal})},
  connectionOptions:(values={},signal)=>resource('/api/connections/options'+query(values),{signal}),
  addConnection:body=>resource('/api/connections',{method:'POST',body}),
  updateConnection:(id,body)=>resource('/api/connections/'+encodeURIComponent(id),{method:'PUT',body}),
  removeConnection:(id,revision)=>resource('/api/connections/'+encodeURIComponent(id)+query({revision}),{method:'DELETE'}),
  checkConnection:id=>resource('/api/connections/'+encodeURIComponent(id)+'/check',{method:'POST'}),
  syncConnection:id=>resource('/api/connections/'+encodeURIComponent(id)+'/sync',{method:'POST'}),
  pages:(values,signal)=>resource('/api/pages'+query(values),{signal}),
  detail:(id,timezone,signal,values={})=>resource('/api/pages/'+encodeURIComponent(id)+query({...values,timezone}),{signal}),
  refreshPage:(id,connectionId,timezone,signal)=>resource('/api/pages/'+encodeURIComponent(id)+'/refresh'+query({timezone}),{method:'POST',body:{...(connectionId?{connection_id:Number(connectionId)}:{}),include_posts:true,limit:25},signal}),
  pageEditOptions:(id,connectionId,signal)=>resource('/api/pages/'+encodeURIComponent(id)+'/edit-options',{method:'POST',body:{connection_id:Number(connectionId)},signal}),
  pageCategories:(id,connectionId,q,signal)=>resource('/api/pages/'+encodeURIComponent(id)+'/categories'+query({connection_id:connectionId,q}),{signal}),
  editPage:(id,body)=>resource('/api/pages/'+encodeURIComponent(id)+'/edit',{method:'POST',body}),
  editPageImage:(id,connectionId,kind,requestId,file)=>resource('/api/pages/'+encodeURIComponent(id)+'/image'+query({connection_id:connectionId,kind,request_id:requestId}),{method:'POST',body:file}),
  tokenReelConfig:signal=>resource('/api/reels/config',{signal}),
  tokenReelAssets:(values={},signal)=>{if(typeof values?.aborted==='boolean'){signal=values;values={}}return resource('/api/reels/assets'+query(values),{signal})},
  tokenReelUpload:(file,signal)=>resource('/api/reels/assets',{method:'POST',body:file.slice(0,file.size,'application/octet-stream'),signal,extraHeaders:{'X-Filename':encodeURIComponent(file.name)}}),
  tokenReelJobs:(values={},signal)=>{if(typeof values?.aborted==='boolean'){signal=values;values={}}return resource('/api/reels'+query(values),{signal})},
  tokenReelDetail:(id,signal)=>resource('/api/reels/'+encodeURIComponent(id),{signal}),
  tokenReelCreate:body=>resource('/api/reels',{method:'POST',body}),
  tokenReelAction:(id,action)=>resource('/api/reels/'+encodeURIComponent(id)+'/'+action,{method:'POST',body:{}}),
  videoSource:(values={},signal)=>resource('/api/video-source'+query(values),{signal}),
  saveVideoSource:body=>resource('/api/video-source/settings',{method:'PUT',body}),
  scanVideoSource:()=>resource('/api/video-source/scan',{method:'POST',body:{}}),
  ideaPipeline:(values={},signal)=>{if(typeof values?.aborted==='boolean'){signal=values;values={}}return resource('/api/idea-pipeline'+query(values),{signal})},
  projectAssets:signal=>resource('/api/idea-pipeline/project-assets',{signal}),
  ideaAutomation:signal=>resource('/api/idea-pipeline/automation',{signal}),
  saveIdeaAutomation:body=>resource('/api/idea-pipeline/automation',{method:'PUT',body}),
  audioMixes:signal=>resource('/api/idea-pipeline/audio-mixes',{signal}),
  approveAudioMixes:body=>resource('/api/idea-pipeline/audio-mixes/approve',{method:'POST',body}),
  scanIdeaPipeline:(values={})=>resource('/api/idea-pipeline/scan'+query(values),{method:'POST',body:{}}),
  runIdea:(id,body)=>resource('/api/idea-pipeline/ideas/'+encodeURIComponent(id)+'/run',{method:'POST',body}),
  cancelIdeaRun:id=>resource('/api/idea-pipeline/runs/'+encodeURIComponent(id)+'/cancel',{method:'POST',body:{}}),
  uploadIdea:file=>resource('/api/idea-pipeline/images',{method:'POST',body:file.slice(0,file.size,'application/octet-stream'),extraHeaders:{'X-Filename':encodeURIComponent(file.name)}}),
  refreshIdeaViews:()=>resource('/api/idea-pipeline/views/refresh',{method:'POST',body:{}}),
  uploaderStatus:()=>resource('/api/idea-pipeline/upload/status'),
  connectUploader:body=>resource('/api/idea-pipeline/upload/connect',{method:'POST',body}),
  disconnectUploader:()=>resource('/api/idea-pipeline/upload/connection',{method:'DELETE'}),
  checkUploaderSession:provider=>resource('/api/idea-pipeline/upload/session-check',{method:'POST',body:{provider}}),
  producerConnections:signal=>resource('/api/idea-pipeline/producers',{signal}),
  checkProducerConnections:provider=>resource('/api/idea-pipeline/producers/check',{method:'POST',body:{provider}}),
  telegramApproval:signal=>resource('/api/telegram-approval',{signal}),
  saveTelegramApproval:body=>resource('/api/telegram-approval',{method:'PUT',body}),
  checkTelegramApproval:revision=>resource('/api/telegram-approval/check',{method:'POST',body:{revision}}),
  trialTelegramApproval:(revision,request_id)=>resource('/api/telegram-approval/trial',{method:'POST',body:{revision,request_id}}),
  disconnectTelegramApproval:revision=>resource('/api/telegram-approval/connection',{method:'DELETE',body:{revision}}),
  events:(values={limit:50,offset:0},signal)=>resource('/api/events'+query(values),{signal}),
};
