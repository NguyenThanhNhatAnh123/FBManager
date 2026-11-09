'use strict';
const content=document.querySelector('#content'),notice=document.querySelector('#notice');
let current=null,csrf='',fieldSequence=0,appInfo={};
function el(tag,attrs={},...children){const node=document.createElement(tag);for(const [k,v] of Object.entries(attrs)){if(k==='text')node.textContent=v;else if(k.startsWith('on'))node.addEventListener(k.slice(2),v);else if(k==='checked')node.checked=v;else node.setAttribute(k,v)}for(const child of children)node.append(child);return node}
function message(value){notice.textContent=value}
async function api(path,method='GET',body){const response=await fetch('/auth/api/'+path,{method,credentials:'same-origin',cache:'no-store',headers:{...(body?{'Content-Type':'application/json'}:{}),...(method!=='GET'?{'X-Via-CSRF':csrf}:{})},...(body?{body:JSON.stringify(body)}:{})});const value=await response.json();if(!response.ok){const error=new Error(value.detail||'Chưa hoàn tất thao tác.');error.status=response.status;throw error}return value}
function field(form,label,name,type='text',auto='off'){const id='input-'+name+'-'+(++fieldSequence);const input=el('input',{id,name,type,required:'',autocomplete:auto,...(type==='password'?{minlength:12,maxlength:128}:{maxlength:40})});form.append(el('label',{for:id,text:label}),input);return input}
async function busy(form,work){const buttons=[...form.querySelectorAll('button')];buttons.forEach(b=>b.disabled=true);message('');try{await work()}catch(error){message(error.message)}finally{buttons.forEach(b=>b.disabled=false)}}
function next(){
 const raw=new URLSearchParams(location.search).get('next');
 if(!raw||!raw.startsWith('/')||raw.startsWith('//')||raw.includes('\\'))return '/';
 const url=new URL(raw,location.origin);
 const screens=['/','/chrome/pages','/chrome/reels/new','/chrome/reels/saved','/chrome/settings',
  '/pages/','/pages/schedule','/pages/saved','/pages/settings','/schedule','/saved','/settings'];
 if(url.origin!==location.origin||!screens.includes(url.pathname))return '/';
 const tabs={
  '/chrome/settings':['app','bm','proxies','automation','history','recipient','data'],
  '/pages/settings':['connections','activity','videosource','ideapipeline'],
  '/settings':['connections','activity','videosource','ideapipeline'],
 };
 const tab=url.searchParams.get('tab');
 return tabs[url.pathname]?.includes(tab)?url.pathname+'?tab='+tab:url.pathname;
}
function login(){
 document.body.classList.add('auth-login');
 document.title='Via · Đăng nhập';
 const heading=el('h1',{id:'login-heading',text:'Đăng nhập Via'});
 const intro=el('p',{class:'login-intro',text:'Mở không gian làm việc của bạn.'});
 const form=el('form');
 const username=field(form,'Tên đăng nhập','username','text','username');
 username.setAttribute('autocapitalize','none');
 username.setAttribute('spellcheck','false');
 const password=field(form,'Mật khẩu','password','password','current-password');
 const toggle=el('button',{type:'button',class:'password-toggle',text:'Hiện',
  'aria-label':'Hiện mật khẩu','aria-controls':password.id});
 toggle.addEventListener('click',()=>{
  const reveal=password.type==='password';
  password.type=reveal?'text':'password';
  toggle.textContent=reveal?'Ẩn':'Hiện';
  toggle.setAttribute('aria-label',reveal?'Ẩn mật khẩu':'Hiện mật khẩu');
 });
 form.append(el('div',{class:'login-password'},password,toggle));
 const submit=el('button',{type:'submit',class:'login-submit',text:'Đăng nhập'});
 form.append(notice,submit);
 form.addEventListener('submit',async event=>{
  event.preventDefault();
  if(submit.disabled)return;
  submit.textContent='Đang đăng nhập…';
  await busy(form,async()=>{
   const value=await api('login','POST',{username:username.value.trim(),password:password.value});
   password.value='';
   location.assign(value.user.must_change_password?'/auth/account':next());
  });
  submit.textContent='Đăng nhập';
 });
 content.replaceChildren(el('section',{class:'login','aria-labelledby':heading.id},heading,intro,form));
 username.focus();
}
async function logout(){try{await api('logout','POST');location.assign('/auth/login')}catch(error){message(error.message)}}
function account(){content.replaceChildren(el('h1',{text:'Tài khoản của bạn'}),el('p',{text:current.username+' · '+(current.role==='admin'?'Quản trị viên':'Người vận hành')}));if(current.must_change_password)content.append(el('p',{class:'status',text:'Đổi mật khẩu ban đầu để mở workspace. Mật khẩu mới có ít nhất 12 ký tự.'}));else{const links=el('nav',{class:'links','aria-label':'Không gian làm việc'});if(!appInfo.portal_only)links.append(el('a',{href:'/',text:'Chrome Manager'}),el('a',{href:'/pages/',text:'Page Manager'}));if(current.role==='admin')links.append(el('a',{href:'/auth/users',text:'Quản lý 3 tài khoản'}));content.append(links)}const form=el('form',{},el('h2',{text:'Đổi mật khẩu'}));const old=field(form,'Mật khẩu hiện tại','current_password','password','current-password'),password=field(form,'Mật khẩu mới','new_password','password','new-password'),confirm=field(form,'Nhập lại mật khẩu mới','confirm_password','password','new-password');form.append(el('button',{type:'submit',text:'Lưu mật khẩu mới'}));form.addEventListener('submit',event=>{event.preventDefault();void busy(form,async()=>{if(password.value!==confirm.value)throw new Error('Hai mật khẩu mới chưa khớp.');await api('password','POST',{current_password:old.value,new_password:password.value});form.reset();await loadMe();account();message('Đã đổi mật khẩu và thu hồi các phiên cũ. Bạn có thể mở workspace.')})});content.append(el('section',{},form),el('button',{type:'button',text:'Đăng xuất',onclick:logout}));if(!current.must_change_password&&appInfo.mcp_url){const holder=el('section');content.append(holder);void renderMcpTokens(holder)}}
async function users(){if(current.role!=='admin'||current.must_change_password){account();return}const value=await api('users');content.replaceChildren(el('h1',{text:'Quản lý người dùng'}),el('p',{text:'Có 3 tài khoản. Thay đổi quyền, mật khẩu hoặc trạng thái sẽ thu hồi các phiên của người đó.'}),el('a',{href:'/auth/account',text:'Tài khoản của tôi'}));const grid=el('div',{class:'account-grid'});for(const user of value.users){const form=el('form',{},el('h2',{text:user.username})),name=field(form,'Tên đăng nhập','username');name.value=user.username;const role=el('select',{'aria-label':'Quyền '+user.username},el('option',{value:'admin',text:'Quản trị viên'}),el('option',{value:'operator',text:'Người vận hành'}));role.value=user.role;const active=el('input',{type:'checkbox',checked:user.active});form.append(el('label',{text:'Quyền'}),role,el('label',{},active,'Cho phép đăng nhập'),el('p',{class:'hint',text:user.must_change_password?'Chờ đổi mật khẩu ban đầu':'Đã đặt mật khẩu riêng'}),el('button',{type:'submit',text:'Lưu thay đổi'}));form.addEventListener('submit',event=>{event.preventDefault();void busy(form,async()=>{await api('users/'+user.id,'PATCH',{username:name.value.trim(),role:role.value,active:active.checked,revision:user.revision});if(user.id===current.id){location.assign('/auth/login');return}await users();message('Đã lưu và thu hồi các phiên cũ của người dùng.')})});const reset=el('button',{type:'button',text:'Đặt lại mật khẩu',onclick:()=>{if(!confirm('Đặt lại mật khẩu và đăng xuất các phiên của '+user.username+'?'))return;void busy(form,async()=>{const result=await api('users/'+user.id,'PATCH',{revision:user.revision,reset_password:true});if(user.id===current.id){location.assign('/auth/login');return}user.revision=result.user.revision;form.querySelector('output')?.remove();form.append(el('output',{text:'Mật khẩu tạm (chỉ hiện lần này):\n'+result.temporary_password+'\nNgười dùng cần đổi mật khẩu khi đăng nhập.'}));message('Đã đặt lại mật khẩu. Chuyển mật khẩu tạm riêng cho đúng người dùng.')})}});if(user.id!==current.id)form.append(reset);grid.append(el('section',{},form))}content.append(grid);await audit()}
async function audit(){const value=await api('audit');const table=el('table'),head=el('tr');for(const name of ['Thời gian','Người dùng','Hành động','Đường dẫn','Kết quả'])head.append(el('th',{text:name}));table.append(el('thead',{},head));const body=el('tbody');for(const row of value.items)body.append(el('tr',{},...[(new Date(row.at*1000)).toLocaleString(),row.actor,row.action,row.route,String(row.status)].map(text=>el('td',{text}))));table.append(body);content.append(el('section',{},el('h2',{text:'Nhật ký thao tác gần đây'}),el('p',{class:'hint',text:'Lưu 90 ngày, không lưu mật khẩu, token hoặc nội dung yêu cầu. 102 là bắt đầu thao tác.'}),el('div',{class:'table-scroll'},table)))}
async function renderMcpTokens(holder){
 try{
  const data=await api('mcp-tokens');
  holder.replaceChildren(el('h2',{text:'Kết nối Codex trên máy của bạn'}),el('p',{text:'Mỗi token thuộc riêng tài khoản này, kế thừa quyền của bạn và có thể thu hồi. Không chia sẻ token với người khác.'}),el('p',{class:'hint',text:'Địa chỉ MCP: '+data.mcp_url}));
  const form=el('form'),name=field(form,'Tên máy hoặc kết nối','connection_name');
  const mode=el('select',{'aria-label':'Quyền kết nối'},el('option',{value:'read',text:'Chỉ đọc'}),el('option',{value:'write',text:'Vận hành theo quyền tài khoản'}));
  const days=el('select',{'aria-label':'Thời hạn token'},el('option',{value:'7',text:'7 ngày'}),el('option',{value:'30',text:'30 ngày'}));
  form.append(el('label',{text:'Quyền kết nối'}),mode,el('label',{text:'Hết hạn sau'}),days,el('button',{type:'submit',text:'Tạo token riêng'}));
  form.addEventListener('submit',event=>{event.preventDefault();void busy(form,async()=>{
   const result=await api('mcp-tokens','POST',{name:name.value.trim(),scopes:mode.value==='write'?['via:read','via:write']:['via:read'],days:Number(days.value)});
   await renderMcpTokens(holder);
   holder.append(el('output',{text:'Token chỉ hiện một lần:\n'+result.token+'\nDán vào bộ cài plugin Via trên máy của bạn.'}));
   message('Đã tạo kết nối Codex riêng cho tài khoản này.');
  })});
  holder.append(form);
  for(const item of data.tokens){
   const active=!item.revoked_at&&item.valid_revision&&item.expires_at*1000>Date.now();
   const line=el('div',{},el('p',{text:item.name+' · '+(active?'Đang hiệu lực':'Đã hết hiệu lực')+' · '+new Date(item.expires_at*1000).toLocaleString()}));
   if(active)line.append(el('button',{type:'button',text:'Thu hồi '+item.name,onclick:()=>{void busy(holder,async()=>{await api('mcp-tokens/'+item.id,'DELETE');await renderMcpTokens(holder);message('Đã thu hồi kết nối Codex.')})}}));
   holder.append(line);
  }
 }catch(error){holder.replaceChildren(el('p',{text:error.message}))}
}
async function loadMe(){const result=await api('me');current=result.user;csrf=result.csrf_token;appInfo=result.app||{}}
(async()=>{try{await loadMe();if(location.pathname==='/auth/login'&&!current.must_change_password){location.replace(next());return}if(location.pathname==='/auth/users')await users();else account()}catch(error){if(error.status===401)login();else message(error.message)}})();
