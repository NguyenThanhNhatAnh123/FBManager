import { useEffect, useState } from 'react'
import { loadIdentity, type IdentityUser } from '../services/identity'

export function IdentityAccount(){
 const [user,setUser]=useState<IdentityUser|null>(null)
 useEffect(()=>{let active=true;void loadIdentity().then(value=>{if(active)setUser(value.user)}).catch(()=>{});return()=>{active=false}},[])
 return <a className="account" href="/auth/account" title="Tài khoản, mật khẩu và đăng xuất"><div>{user?.username||'Tài khoản'}<small>{user?.role==='admin'?'Quản trị viên':'Người vận hành'}</small></div></a>
}
