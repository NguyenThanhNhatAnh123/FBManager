import { useEffect, useState, type FormEvent } from 'react'
import { Save, ShieldCheck, Cpu, Globe } from 'lucide-react'
import { api } from '../services/api'
import type { SettingsData, Notify } from '../services/types'
import { FacebookRefreshSettings } from './FacebookRefreshSettings'
export function SettingsPage({notify,refresh}:{notify:Notify;refresh:()=>void}){
 const [settings,setSettings]=useState<SettingsData|null>(null),[origins,setOrigins]=useState(''),[busy,setBusy]=useState(false),[error,setError]=useState('')
 useEffect(()=>{api<SettingsData>('/settings').then(s=>{setSettings(s);setOrigins(s.allowed_origins.join('\n'))}).catch(e=>setError(e.message))},[])
 async function reload(){setBusy(true);try{const s=await api<SettingsData>('/settings');setSettings(s);setOrigins(s.allowed_origins.join('\n'));notify('Đã tải lại cài đặt mới nhất.')}catch(e){notify((e as Error).message,true)}finally{setBusy(false)}}
 if(error)return <div className="warning">{error}</div>
 if(!settings)return <div className="panel loading">Loading settings…</div>
 function number(key:keyof SettingsData,value:string){setSettings(s=>s?{...s,[key]:Number(value)}:s)}
 async function save(e:FormEvent){e.preventDefault();setBusy(true);try{const s=await api<SettingsData>('/settings','PUT',{...settings,allowed_origins:origins.split('\n').map(s=>s.trim()).filter(Boolean)});setSettings(s);setOrigins(s.allowed_origins.join('\n'));notify('Settings saved');refresh()}catch(e){notify((e as Error).message,true)}finally{setBusy(false)}}
 const fields:[keyof SettingsData,string,number,number,string][]=[
 ['max_browsers','Concurrent browsers',1,100,'Default 5. Choose 1, 5, 10, 20 or up to 100 if your hardware allows.'],
 ['max_memory_percent','Pause launches at RAM usage (%)',20,98,'Existing browsers stay open; queued launches wait.'],
 ['min_available_memory_mb','Minimum free RAM (MB)',128,131072,'New browsers wait when available memory falls below this value.'],
 ['max_cpu_percent','Pause launches at CPU usage (%)',20,100,'Checked every two seconds.'],
 ['action_delay_ms','Per-origin action delay (ms)',500,60000,'A shared rate limit across all profiles testing the same origin.'],
 ['launch_timeout_seconds','Browser launch timeout (seconds)',10,180,'Limit the time spent starting Chrome.'],
 ['task_timeout_seconds','Task wall timeout (seconds)',10,3600,'Includes time waiting in the browser queue.']]
 return <form onSubmit={save} className="settings-form"><section className="panel settings-section"><div className="section-icon"><Cpu size={20}/></div><div><h2>Resource controls</h2><p className="muted">Keep the machine responsive as your workspace grows.</p><div className="settings-grid">{fields.map(([key,label,min,max,hint])=><label key={key}>{label}<input type="number" required min={min} max={max} value={Number(settings[key])} onChange={e=>number(key,e.target.value)}/><small>{hint}</small></label>)}</div></div></section>
 <section className="panel settings-section"><div className="section-icon"><Globe size={20}/></div><div><h2>Authorized websites</h2><p className="muted">Only these exact origins can receive requests during automation.</p><label>One origin per line<textarea rows={5} required value={origins} onChange={e=>setOrigins(e.target.value)} placeholder={'https://your-site.com\nhttps://static.your-site.com'}/></label><p className="hint">Include required subdomains, redirects, ports and asset hosts. Adding an origin confirms you have permission to test it.</p></div></section>
 <section className="panel settings-section"><div className="section-icon"><ShieldCheck size={20}/></div><div><h2>Connection policy</h2>
 <label>Proxy routing<select value={settings.proxy_routing||'all'} onChange={e=>setSettings({...settings,proxy_routing:e.target.value as SettingsData['proxy_routing']})}><option value="all">All websites through the profile proxy</option><option value="facebook_only">Facebook only · Other websites direct</option></select><small className="hint">Stop all browsers and tasks before changing this setting. Applies to manual browsing and automation.</small></label>
 <p className="hint">{settings.proxy_routing==='facebook_only'?'Facebook, Messenger, fbcdn.net, facebook.net, fbsbx.com and supported Facebook link/login domains use the assigned proxy. Outlook and all other domains use your direct internet connection. A failed Facebook proxy connection stays blocked.':'All destinations use the assigned proxy. Failed proxy connections stay blocked.'}</p>
 <label>IP version to check<select value={settings.proxy_ip_version||'auto'} onChange={e=>setSettings({...settings,proxy_ip_version:e.target.value as SettingsData['proxy_ip_version']})}><option value="auto">Auto · IPv4 or IPv6</option><option value="ipv4">IPv4 only</option><option value="ipv6">IPv6 only</option></select><small className="hint">Applies to the ordinary proxy pool. BM private proxy checks always use Auto IPv4/IPv6.</small></label>
 <label className="check-label"><input type="checkbox" checked={settings.require_proxy} onChange={e=>setSettings({...settings,require_proxy:e.target.checked})}/>Require an assigned proxy before opening Chrome</label><p className="hint">Profiles without an assigned proxy can use the direct connection only when this requirement is disabled.</p><div className="security-note"><ShieldCheck size={16}/>Local access only · Windows DPAPI · Isolated browser storage</div></div></section>
 <FacebookRefreshSettings enabled={settings.facebook_auto_refresh??true} disabled={busy} onEnabledChange={value=>setSettings({...settings,facebook_auto_refresh:value})}/>
 <div className="settings-save"><button className="button" type="button" disabled={busy} onClick={()=>void reload()}>Tải lại cài đặt</button><button className="button primary" disabled={busy}><Save size={16}/>{busy?'Saving…':'Save settings'}</button></div></form>
}
