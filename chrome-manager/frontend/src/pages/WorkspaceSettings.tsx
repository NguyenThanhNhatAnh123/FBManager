import type { KeyboardEvent, ReactNode } from 'react'
import { Building2, Cable, Database, History, Settings, UserPlus, Workflow } from 'lucide-react'

export type SettingsSection='bm'|'proxies'|'automation'|'history'|'recipient'|'data'|'app'

const sections=[
 {id:'bm',label:'BM Profiles',detail:'Profile cầm BM và proxy riêng',icon:Building2},
 {id:'proxies',label:'Proxy Manager',detail:'Kết nối và kiểm tra proxy',icon:Cable},
 {id:'automation',label:'Automation Tasks',detail:'Tạo và quản lý tác vụ',icon:Workflow},
 {id:'history',label:'Task History',detail:'Kết quả và nhật ký chạy',icon:History},
 {id:'recipient',label:'Nhận Page & Setup',detail:'Lời mời và thông tin Page',icon:UserPlus},
 {id:'data',label:'Dữ liệu & công cụ',detail:'Outlook, Authenticator và nhập dữ liệu',icon:Database},
 {id:'app',label:'Cài đặt ứng dụng',detail:'Tài nguyên và chính sách kết nối',icon:Settings},
] as const

export function WorkspaceSettings({section,onSection,children}:{section:SettingsSection;onSection:(section:SettingsSection)=>void;children:ReactNode}){
 function navigate(event:KeyboardEvent<HTMLButtonElement>,index:number){
  const next=['ArrowRight','ArrowDown'].includes(event.key)?(index+1)%sections.length:['ArrowLeft','ArrowUp'].includes(event.key)?(index-1+sections.length)%sections.length:event.key==='Home'?0:event.key==='End'?sections.length-1:null
  if(next===null)return
  event.preventDefault();onSection(sections[next].id)
  document.getElementById('settings-tab-'+sections[next].id)?.focus()
 }
 return <div className="workspace-settings">
  <div className="settings-subnav" role="tablist" aria-label="Nhóm Settings" aria-orientation="horizontal">
   {sections.map((item,index)=><button key={item.id} type="button" role="tab" id={'settings-tab-'+item.id} aria-label={item.label} aria-describedby={'settings-description-'+item.id}
    aria-controls={'settings-panel-'+item.id} aria-selected={section===item.id} tabIndex={section===item.id?0:-1} className={section===item.id?'active':''}
    onKeyDown={event=>navigate(event,index)} onClick={()=>onSection(item.id)}><item.icon size={17}/><span>{item.label}<small id={'settings-description-'+item.id}>{item.detail}</small></span></button>)}
  </div>
  <section className="settings-content" role="tabpanel" id={'settings-panel-'+section} aria-labelledby={'settings-tab-'+section} tabIndex={0}>
   {children}
  </section>
 </div>
}
