export type IdentityUser={id:number;username:string;role:'admin'|'operator';active:boolean;must_change_password:boolean;revision:number}
type Identity={user:IdentityUser;csrf_token:string}
let identity:Identity|null=null
let pending:Promise<Identity>|null=null

export function identityRedirect(response:Pick<Response,'status'>,code?:unknown){
 if(response.status===401){
  const {page,settingsSection}=chromeLocation()
  location.assign('/auth/login?next='+encodeURIComponent(chromeUrl(page,settingsSection||'app')))
 }else if(code==='password_change_required')location.assign('/auth/account')
}
export async function loadIdentity():Promise<Identity>{
 if(pending)return pending
 pending=(async()=>{
  const response=await fetch('/auth/api/me',{credentials:'same-origin',cache:'no-store'})
  if(response.status===401){identityRedirect(response);throw new Error('Đăng nhập để tiếp tục.')}
  if(!response.ok)throw new Error('Chưa đọc được tài khoản. Thử tải lại trang.')
  const value=await response.json() as Identity
  if(value.user.must_change_password){location.assign('/auth/account');throw new Error('Đổi mật khẩu ban đầu để tiếp tục.')}
  identity=value;return value
 })()
 try{return await pending}finally{pending=null}
}
export async function identityHeaders():Promise<Record<string,string>>{
 const value=identity||await loadIdentity()
 return {'X-Via-CSRF':value.csrf_token}
}
import { chromeLocation, chromeUrl } from './navigation'

