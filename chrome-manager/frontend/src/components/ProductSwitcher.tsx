import { Globe, Layers } from 'lucide-react'
import './product-switcher.css'

export function ProductSwitcher(){
 return <div className="cm-products" role="navigation" aria-label="Chuyển ứng dụng">
  <a className="active" href={window.location.origin} onClick={event=>event.preventDefault()} aria-current="page" title="Chrome Manager"><Globe size={14}/><span>Chrome</span></a>
  <a href="/pages/" rel="noreferrer" title="Page Manager · Dữ liệu riêng"><Layers size={14}/><span>Page Manager</span></a>
 </div>
}
